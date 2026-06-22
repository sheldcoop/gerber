"""
views/cm_render.py — design-layer rendering helpers for the Unit Commonality view.

Owns the "how a layer is drawn" concern: colour assignment, draw-order, opacity, the
SVG-url cache, and placing each layer as a Plotly background image. Split out of
unit_commonality.py so the view module stays a thin orchestrator.
"""

import logging
import threading

import streamlit as st
import plotly.graph_objects as go
from typing import Any, Tuple

from core.svg_utils import build_layer_url, stable_layer_colors, LayerStyle

logger = logging.getLogger(__name__)
from core.constants import (
    LAYER_Z as _LAYER_Z,
    COPPER_TYPES as _COPPER_TYPES,
    COPPER_STACK_ORDER as _COPPER_STACK_ORDER,
    LAYER_PALETTE as _MULTI_LAYER_COLORS,
    LAYER_OPACITY_SINGLE as _LAYER_OPACITY_SINGLE,
    LAYER_OPACITY_MULTI as _LAYER_OPACITY_MULTI,
)


def _layer_color_map(rodb) -> dict:
    """Stable name→hue map so each layer keeps its colour regardless of selection."""
    if not rodb or not getattr(rodb, 'layers', None):
        return {}
    return stable_layer_colors(rodb.layers.keys())


def _copper_draw_rank(name: str) -> int:
    """Within-copper draw rank: higher = nearer the top of the displayed stack."""
    n = name.upper()
    for i, key in enumerate(_COPPER_STACK_ORDER):
        if n == key or n.startswith(key):
            return i + 1
    return 0  # unknown copper → below the recognised stack


def _layer_sort_key(name_lyr_pair: Tuple[str, Any]) -> Tuple[int, int]:
    name, lyr = name_lyr_pair
    z = _LAYER_Z.get(lyr.layer_type, 1)
    sub = _copper_draw_rank(name) if lyr.layer_type in _COPPER_TYPES else 0
    return (z, sub)


def _layer_opacity(layer_name: str, lyr_type: str, multi: bool,
                   dense_n: int = 1, outline: bool = False) -> float:
    """Effective Plotly image opacity for one layer.

    Honours the per-layer sidebar slider, but when several dense copper pours are
    stacked it caps the opacity so the topmost layer can't fully occlude the ones
    beneath. Outline mode doesn't occlude, so it renders at full strength.
    """
    if outline:
        slider_val = st.session_state.get(f"opacity_{layer_name}")
        return float(slider_val) if slider_val is not None else 1.0

    slider_val = st.session_state.get(f"opacity_{layer_name}")
    base = (float(slider_val) if slider_val is not None
            else (_LAYER_OPACITY_MULTI if multi else _LAYER_OPACITY_SINGLE)
            .get(lyr_type, 0.70 if multi else 0.85))

    # Dense copper occludes: cap opacity as the stack grows so lower layers show.
    if lyr_type in _COPPER_TYPES and dense_n > 1:
        cap = max(0.35, 1.1 / dense_n)
        return min(base, cap)
    return base


def _svg_cache_key(name, rot_deg, is_multi, style: LayerStyle):
    """Stable cache key shared by _svg_url and the pre-warmer (must stay in lock-step)."""
    return (name, round(rot_deg, 1), bool(is_multi)) + style.cache_key()


def _svg_cache_dict():
    """Return the per-session styled-SVG cache dict, (re)created per loaded board."""
    gen = id(st.session_state.get('rendered_odb'))
    store = st.session_state.get('_cm_svg_cache')
    if store is None or store.get('gen') != gen:
        store = {'gen': gen, 'data': {}}
        st.session_state['_cm_svg_cache'] = store
    return store['data']


def _svg_url(lyr_obj: Any, rot_deg: float, is_multi: bool, style: LayerStyle) -> str:
    """Reference-layer SVG url for one design layer in the given render `style`.

    Rotating a multi-megabyte copper SVG costs ~15-20 ms and runs on every Streamlit
    rerun for every shown layer on a rotated panel. The un-rotated/plain path is O(1)
    (precomputed data URL). For the rotating/recolouring/outline paths we memoise the
    result per session, keyed by (name, rotation, multi, style). The cache is tied to the
    current rendered_odb object identity so it auto-resets when a new TGZ is uploaded.
    """
    name = getattr(lyr_obj, 'name', None)
    _plain = not style.invert and not style.layer_color and not style.outline
    if (abs(rot_deg) < 0.01 and _plain) or name is None:
        return build_layer_url(lyr_obj, rot_deg, is_multi, style)

    cache = _svg_cache_dict()
    key = _svg_cache_key(name, rot_deg, is_multi, style)
    url = cache.get(key)
    if url is None:
        url = build_layer_url(lyr_obj, rot_deg, is_multi, style)
        cache[key] = url
    return url


def _single_layer_style(layer_type, color):
    """The render style a layer gets when shown on its own (the common first click).

    Returns None for layers whose single-layer URL doesn't use the cache anyway (drill is
    the precomputed O(1) path), so we don't waste work pre-warming them.
    """
    if layer_type in _COPPER_TYPES:
        return LayerStyle(layer_color=color, outline=True, filled=True)   # copper wireframe-on-field
    if layer_type == 'soldermask':
        return LayerStyle(layer_color=color)                              # solid colour
    return None  # drill/other → plain/precomputed path, already instant


def prewarm_layer_urls(rodb, swap_angle: float, manual_rot: float = 0.0) -> None:
    """Background-build the single-layer styled SVG URLs so the FIRST click on any layer
    is already warm — without blocking the initial render.

    Safe by construction: the worker thread calls only the pure `build_layer_url` and
    writes into the session SVG-cache dict (a plain dict; GIL-safe setitem). It never
    touches Streamlit APIs. Runs once per loaded board, keys match `_svg_url` exactly.
    """
    if not rodb or not getattr(rodb, 'layers', None):
        return
    gen = id(rodb)
    if st.session_state.get('_cm_prewarm_gen') == gen:
        return
    st.session_state['_cm_prewarm_gen'] = gen  # set before spawning → runs once even on error

    cache = _svg_cache_dict()  # main-thread access; worker only mutates the returned dict
    color_map = stable_layer_colors(rodb.layers.keys())
    svg_rot = (round(swap_angle) + manual_rot) % 360

    jobs = []
    for name, lyr in rodb.layers.items():
        style = _single_layer_style(lyr.layer_type, color_map.get(name))
        if style is None:
            continue
        key = _svg_cache_key(name, svg_rot, False, style)
        if key not in cache:
            jobs.append((lyr, style, key))
    if not jobs:
        return

    def _worker():
        for lyr, style, key in jobs:
            if key in cache:
                continue
            try:
                cache[key] = build_layer_url(lyr, svg_rot, False, style)
            except Exception:
                logger.debug("prewarm failed for %s", getattr(lyr, 'name', '?'), exc_info=True)

    threading.Thread(target=_worker, name="cm-prewarm", daemon=True).start()


# Tolerance (mm) for deciding the unit cell is genuinely bigger than the copper
# bbox — i.e. we have a real board profile rather than the copper-bbox fallback.
_UNIT_FRAME_TOL = 0.5


def _design_anchor(copper_bounds, cell_w, cell_h):
    """Decide how to anchor + size the reference design overlay.

    Returns (ref_shift, ref_w, ref_h).

    The defect cloud is placed in the unit's step-origin frame (ALIGNED = X_MM −
    step_origin), so it fills the whole unit cell [0, cell_w] × [0, cell_h]. To make
    the design overlay register with the defects we have two regimes:

    • Unit frame (a real board profile exists → the cell is larger than the copper
      bbox): anchor the design at its NATIVE unit-local coordinates (shift = 0) and
      use the unit cell as the reference footprint. Copper then sits where it
      physically is inside the unit — surrounded by dielectric — instead of being
      jammed into the bottom-left corner, so defects land on the real copper.

    • Legacy fallback (no usable profile → cell ≈ copper bbox): keep the old
      behaviour of shifting the copper bbox corner to the origin so the artwork
      fills the view. Defect/design registration is unchanged for these boards.
    """
    cmnx, cmny, cmxx, cmxy = copper_bounds
    copper_w, copper_h = cmxx - cmnx, cmxy - cmny
    if cell_w >= copper_w + _UNIT_FRAME_TOL or cell_h >= copper_h + _UNIT_FRAME_TOL:
        return (0.0, 0.0), cell_w, cell_h
    return (-cmnx, -cmny), copper_w, copper_h


def _layer_placement(b, ref_shift, ref_w, ref_h, swap_angle, svg_rot):
    """Pure geometry: where to anchor a layer's image (Plotly top-left x,y + size).

    Returns (x, y, szx, szy). The layer's TRUE centre in the (unrotated) reference cell is
    rotated about the cell centre into the swapped canvas, so sub-region layers (drill/via)
    keep their real offset under a 90/270 panel rotation. At 0/180 this is the identity
    placement (board + shift), unchanged from before. The reference (full-board) layer maps
    to exactly the previous output at every angle — copper is provably unaffected.

    ref_w/ref_h are the UNROTATED reference footprint (W, H); the swapped canvas is [0,H]×[0,W].
    """
    im_w, im_h = b[2] - b[0], b[3] - b[1]
    sx, sy = ref_shift

    # Layer centre in the unrotated, shifted cell frame.
    cx = b[0] + sx + im_w / 2.0
    cy = b[1] + sy + im_h / 2.0

    ang = round(swap_angle) % 360
    if ang == 90:
        fcx, fcy = ref_h - cy, cx
    elif ang == 270:
        fcx, fcy = cy, ref_w - cx
    else:  # 0 / 180 — identity centre (matches the pre-existing non-swap path)
        fcx, fcy = cx, cy

    # If the SVG *content* is rotated 90/270, its viewBox dimensions are swapped.
    svg_is_swapped = round(svg_rot) % 360 in (90, 270)
    szx, szy = (im_h, im_w) if svg_is_swapped else (im_w, im_h)

    # Plotly anchors images at top-left.
    return fcx - szx / 2.0, fcy + szy / 2.0, szx, szy


def _place_layer_image(fig, layer_name, lyr, ref_shift, svg_rot, swap_angle, is_multi,
                       ref_w, ref_h,
                       layer_color: str = None, outline: bool = False, dense_n: int = 1):
    """Add one reference-design layer as a Plotly background image.

    Single source of the layer-placement + 90/270 rotation convention shared by the
    empty-state, empty-layers, and defect-state overlays.
    """
    x, y, szx, szy = _layer_placement(lyr.bounds, ref_shift, ref_w, ref_h, swap_angle, svg_rot)

    # Invert polarity applies to copper and soldermask only — never to drill/via layers.
    _inv = st.session_state.get('invert_polarity', False) and lyr.layer_type != 'drill'

    # Copper contour fill style: a SINGLE copper layer gets the opaque coloured field
    # (looks like real copper); 2+ stacked copper layers use the transparent wireframe so
    # every layer stays visible. Invert flips a single layer to the wireframe too.
    style = LayerStyle(
        layer_color=layer_color,
        outline=outline,
        filled=(outline and dense_n <= 1 and not _inv),
        invert=_inv,
    )

    fig.add_layout_image(dict(
        source=_svg_url(lyr, svg_rot, is_multi, style),
        xref="x", yref="y", x=x, y=y, sizex=szx, sizey=szy,
        sizing="stretch", layer="below",
        opacity=_layer_opacity(layer_name, lyr.layer_type, is_multi,
                               dense_n=dense_n, outline=outline),
    ))


def _place_pairs(fig, pairs, ref_shift, rot, swap_angle, is_multi, color_map, ref_w, ref_h):
    """Draw an ordered list of (name, layer) pairs as background images.

    Colour: copper and soldermask layers render in their assigned distinct hue (single OR
    multi) so the picture matches the sidebar swatch; drill keeps its natural gold.
    Copper always renders as a coloured wireframe (contour) so pour layers show their
    design, not a solid block.

    ref_w/ref_h are the unrotated reference footprint, used to place sub-region layers
    (drill/via) correctly when the unit is rotated 90/270.
    """
    dense_n = sum(1 for _, l in pairs if l.layer_type in _COPPER_TYPES)
    for _n, _l in pairs:
        is_copper = _l.layer_type in _COPPER_TYPES
        _lc = None if _l.layer_type == 'drill' else color_map.get(_n)
        _ol = is_copper
        _place_layer_image(fig, _n, _l, ref_shift, rot, swap_angle, is_multi,
                           ref_w, ref_h, layer_color=_lc, outline=_ol, dense_n=dense_n)


def _display_dims(cell_w, cell_h, angle):
    """Return the on-screen (panel-oriented) cell dimensions.

    `compute_cm_geometry` reports the unit's NATIVE (un-rotated) footprint. For a unit
    placed at 90/270 the defects span the swapped (portrait) footprint, so we only swap
    the *display* dimensions and rotate the reference DESIGN to match.
    """
    return (cell_h, cell_w) if round(angle) % 360 in (90, 270) else (cell_w, cell_h)


def _add_dim_annotations(fig: go.Figure, cw: float, ch: float,
                         layer_label: str = None, panel_label: str = None) -> None:
    """Render a compact info card (W×H, active layer, active panel) in the lower-right."""
    lines = [f"📐 {cw:.2f} × {ch:.2f} mm"]
    if layer_label:
        lines.append(f"🗂 {layer_label}")
    if panel_label:
        lines.append(f"🖥 {panel_label}")
    text = "<br>".join(lines)

    fig.add_annotation(
        x=1.02, y=0.0,
        xref="paper", yref="paper",
        xanchor="left", yanchor="bottom",
        text=text,
        showarrow=False,
        bgcolor="rgba(6,18,6,0.82)",
        bordercolor="rgba(0,200,100,0.45)",
        borderwidth=1,
        borderpad=6,
        font=dict(color="rgba(0,220,130,0.95)", size=11, family="monospace"),
        align="left",
    )


def _add_grid(fig, cell_w, cell_h, step=5.0):
    _g = step
    while _g < cell_w:
        fig.add_shape(type="line", x0=_g, y0=0, x1=_g, y1=cell_h,
                      line=dict(color="rgba(255,255,255,0.06)", width=1), layer="below")
        _g += step
    _g = step
    while _g < cell_h:
        fig.add_shape(type="line", x0=0, y0=_g, x1=cell_w, y1=_g,
                      line=dict(color="rgba(255,255,255,0.06)", width=1), layer="below")
        _g += step


