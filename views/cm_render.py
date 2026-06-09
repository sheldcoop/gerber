"""
views/cm_render.py — design-layer rendering helpers for the Unit Commonality view.

Owns the "how a layer is drawn" concern: colour assignment, draw-order, opacity, the
SVG-url cache, and placing each layer as a Plotly background image. Split out of
unit_commonality.py so the view module stays a thin orchestrator.
"""

import numpy as np
import streamlit as st
import plotly.graph_objects as go
from typing import Any, Tuple

from core.svg_utils import build_rotated_svg_url, stable_layer_colors
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


def _svg_url(lyr_obj: Any, rot_deg: float, is_multi: bool,
             layer_color: str = None, outline: bool = False, invert: bool = False,
             filled: bool = False) -> str:
    """Reference-layer SVG url honouring the invert-polarity toggle.

    Rotating a multi-megabyte copper SVG costs ~15-20 ms and runs on every Streamlit
    rerun for every shown layer on a rotated panel. The un-rotated path is already O(1)
    (precomputed data URL). For the rotating/recolouring paths we memoise the result per
    session, keyed by the full style tuple. The cache is tied to the current rendered_odb
    object identity so it auto-resets when a new TGZ is uploaded.
    """
    name = getattr(lyr_obj, 'name', None)
    if (abs(rot_deg) < 0.01 and not invert and not layer_color and not outline) or name is None:
        return build_rotated_svg_url(lyr_obj, rot_deg, is_multi, invert=invert,
                                     layer_color=layer_color, outline=outline, filled=filled)

    gen = id(st.session_state.get('rendered_odb'))
    store = st.session_state.get('_cm_svg_cache')
    if store is None or store.get('gen') != gen:
        store = {'gen': gen, 'data': {}}
        st.session_state['_cm_svg_cache'] = store
    cache = store['data']

    key = (name, round(rot_deg, 1), bool(invert), bool(is_multi), layer_color,
           bool(outline), bool(filled))
    url = cache.get(key)
    if url is None:
        url = build_rotated_svg_url(lyr_obj, rot_deg, is_multi, invert=invert,
                                    layer_color=layer_color, outline=outline, filled=filled)
        cache[key] = url
    return url


def _place_layer_image(fig, layer_name, lyr, ref_shift, svg_rot, swap, is_multi,
                       layer_color: str = None, outline: bool = False, dense_n: int = 1):
    """Add one reference-design layer as a Plotly background image.

    Single source of the layer-placement + 90/270 dimension-swap convention shared by
    the empty-state, empty-layers, and defect-state overlays.
    """
    b = lyr.bounds
    im_w, im_h = b[2] - b[0], b[3] - b[1]
    sx, sy = ref_shift

    # Center of the layer in the standard (unswapped) coordinate system
    layer_cx = b[0] + sx + im_w / 2.0
    layer_cy = b[3] + sy - im_h / 2.0

    # If the canvas itself is swapped (e.g. panel is rotated 90 deg), the center moves
    if swap:
        final_cx, final_cy = im_h / 2.0, im_w / 2.0
    else:
        final_cx, final_cy = layer_cx, layer_cy

    # If the SVG content is rotated 90/270, its viewBox dimensions are swapped
    svg_is_swapped = round(svg_rot) % 360 in (90, 270)
    if svg_is_swapped:
        szx, szy = im_h, im_w
    else:
        szx, szy = im_w, im_h

    # Plotly anchors images at top-left by default
    x = final_cx - szx / 2.0
    y = final_cy + szy / 2.0

    # Invert polarity applies to copper and soldermask only — never to drill/via layers.
    _inv = st.session_state.get('invert_polarity', False) and lyr.layer_type != 'drill'

    # Copper contour fill style: a SINGLE copper layer gets the opaque coloured field
    # (looks like real copper); 2+ stacked copper layers use the transparent wireframe so
    # every layer stays visible. Invert flips a single layer to the wireframe too.
    filled = outline and dense_n <= 1 and not _inv

    fig.add_layout_image(dict(
        source=_svg_url(lyr, svg_rot, is_multi, layer_color=layer_color,
                        outline=outline, invert=_inv, filled=filled),
        xref="x", yref="y", x=x, y=y, sizex=szx, sizey=szy,
        sizing="stretch", layer="below",
        opacity=_layer_opacity(layer_name, lyr.layer_type, is_multi,
                               dense_n=dense_n, outline=outline),
    ))


def _place_pairs(fig, pairs, ref_shift, rot, swap, is_multi, color_map):
    """Draw an ordered list of (name, layer) pairs as background images.

    Colour: copper and soldermask layers render in their assigned distinct hue (single OR
    multi) so the picture matches the sidebar swatch; drill keeps its natural gold.
    Copper always renders as a coloured wireframe (contour) so pour layers show their
    design, not a solid block.
    """
    dense_n = sum(1 for _, l in pairs if l.layer_type in _COPPER_TYPES)
    for _n, _l in pairs:
        is_copper = _l.layer_type in _COPPER_TYPES
        _lc = None if _l.layer_type == 'drill' else color_map.get(_n)
        _ol = is_copper
        _place_layer_image(fig, _n, _l, ref_shift, rot, swap, is_multi,
                           layer_color=_lc, outline=_ol, dense_n=dense_n)


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


def _add_density_heatmap(fig, cm_plot, cell_w, cell_h):
    try:
        _hx = cm_plot['ALIGNED_X'].dropna().values
        _hy = cm_plot['ALIGNED_Y'].dropna().values
        _nx, _ny = 60, 60
        _gx = np.linspace(0, cell_w, _nx)
        _gy = np.linspace(0, cell_h, _ny)
        _z, _, _ = np.histogram2d(_hy, _hx, bins=[_ny, _nx],
                                  range=[[0, cell_h], [0, cell_w]])
        from scipy.ndimage import gaussian_filter as _gf
        _z = _gf(_z.astype(float), sigma=2.0)
        fig.add_trace(go.Heatmap(z=_z, x=_gx, y=_gy, colorscale='Hot',
                                 opacity=0.55, showscale=False, hoverinfo='skip'))
    except Exception:
        st.warning("Heatmap requires scipy. Install with: pip install scipy")
