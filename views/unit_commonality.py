import streamlit as st
import plotly.graph_objects as go
from typing import Any, Tuple, List

from core.data_utils import compute_cm_geometry, filter_aoi_cm, _align_defects
from visualizer import OverlayConfig, build_defect_only_figure, _apply_layout
from export import export_current_view

from views.cm_render import (
    _place_pairs, _layer_color_map, _layer_sort_key, _display_dims,
    _add_dim_annotations, _add_grid, prewarm_layer_urls, _design_anchor,
)
from views.cm_geometry import _select_units, _compute_origins


def _render_sidebar_controls(rodb_cm_check: Any) -> List[Tuple[str, Any]]:
    """Reads the sidebar layer selection from session state and returns checked layers.

    The actual checkboxes/opacity sliders are rendered in ui/sidebar.py; here we only
    read the `vis_<layer>` session-state flags.
    """
    if not rodb_cm_check or not rodb_cm_check.layers:
        return []

    return [
        (ln, rodb_cm_check.layers[ln])
        for ln in sorted(rodb_cm_check.layers.keys())
        if st.session_state.get(f"vis_{ln}", False)
    ]


# ---------------------------------------------------------------------------
# Empty state — design preview when no AOI data is loaded
# ---------------------------------------------------------------------------

def _render_empty_state(rodb_cm_check: Any, na_checked: List[Tuple[str, Any]]) -> None:
    """Renders the TGZ design preview when no AOI defect data is loaded."""
    st.info("ℹ️ Upload AOI defect data to overlay defects on the design.")
    if not rodb_cm_check or not rodb_cm_check.layers:
        return

    if not na_checked:
        st.caption("☝️ Select a layer in the sidebar to view the design.")
        return

    _ref_lyr = next(
        (l for l in rodb_cm_check.layers.values() if l.layer_type != 'drill'),
        next(iter(rodb_cm_check.layers.values()))
    )

    if rodb_cm_check.panel_layout:
        _, cw, ch = compute_cm_geometry(
            unit_positions=tuple(rodb_cm_check.panel_layout.unit_positions),
            first_layer_bounds=tuple(_ref_lyr.bounds),
            unit_bounds=rodb_cm_check.panel_layout.unit_bounds,
        )
    else:
        _rb = _ref_lyr.bounds
        cw = _rb[2] - _rb[0]
        ch = _rb[3] - _rb[1]

    # Anchor + size the design from the UNROTATED cell dims (before the swap below).
    _ref_shift, _ref_w, _ref_h = _design_anchor(_ref_lyr.bounds, cw, ch)
    _is_multi = len(na_checked) > 1
    _sorted = sorted(na_checked, key=_layer_sort_key)

    _unit_angle = getattr(rodb_cm_check.panel_layout, 'dominant_angle', 0.0) if rodb_cm_check.panel_layout else 0.0
    _rot_deg = float(round(_unit_angle) % 360)
    _swap = _rot_deg in (90.0, 270.0)
    # At 90/270 the unit footprint is rotated — swap canvas dims once (not per layer).
    if _swap:
        cw, ch = ch, cw
    fig = go.Figure()
    _place_pairs(fig, _sorted, _ref_shift, _rot_deg, _rot_deg, _is_multi,
                 _layer_color_map(rodb_cm_check), _ref_w, _ref_h)

    _lbl = " + ".join(n for n, _ in na_checked)
    _add_dim_annotations(fig, cw, ch, _lbl)
    fig.update_layout(
        xaxis=dict(range=[-1, cw + 1], scaleanchor='y', scaleratio=1,
                   showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(range=[-1, ch + 1], showgrid=False,
                   zeroline=False, showticklabels=False),
        plot_bgcolor='#000000', paper_bgcolor='#000000',
        font=dict(color='#cccccc'),
        margin=dict(l=0, r=0, t=36, b=0), height=800,
    )
    st.plotly_chart(fig, width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})


# ---------------------------------------------------------------------------
# Reference design layer overlay
# ---------------------------------------------------------------------------

def _overlay_reference_layers(fig, rodb, svg_rot, swap_angle, first_lyr, cfg,
                              cell_w, cell_h):
    """Draw checked reference design layers under the defect cloud. Returns the
    active (top-most) layer name, or None.

    svg_rot: degrees to rotate the SVG content (auto panel angle + manual nudge).
    swap_angle: the auto panel angle (orthogonal) that decides the 90/270 placement swap.
    cell_w/cell_h: UNROTATED unit cell dims — used to anchor the design in the unit
    frame so it registers with the defects (see _design_anchor).
    """
    if not (rodb and rodb.layers and first_lyr):
        return None

    cam_layers = [n for n in rodb.layers if st.session_state.get(f"vis_{n}", False)]
    is_multi = len(cam_layers) > 1
    pairs = [(ln, rodb.layers[ln]) for ln in cam_layers if rodb.layers.get(ln)]
    pairs.sort(key=_layer_sort_key)
    if not pairs:
        return None
    active = pairs[-1][0]

    ref_shift, _ref_w, _ref_h = _design_anchor(first_lyr.bounds, cell_w, cell_h)
    _place_pairs(fig, pairs, ref_shift, svg_rot, swap_angle, is_multi,
                 _layer_color_map(rodb), _ref_w, _ref_h)
    _apply_layout(fig, cfg)
    return active


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _render_metrics(sel_units, cm_plot):
    c1, c2, c3 = st.columns(3)
    c1.metric("Units Selected", len(sel_units))
    c2.metric("Defects Shown", len(cm_plot))
    c3.metric("Avg / Unit", f"{len(cm_plot)/max(len(sel_units),1):.1f}")


# ---------------------------------------------------------------------------
# Defect state — the interactive superposition view
# ---------------------------------------------------------------------------

def _render_defect_state(rodb, aoi, align_args):
    sel_units, all_pairs, q_rows, q_cols, gap_x, gap_y = _select_units(rodb, aoi)
    if not sel_units:
        st.info("Select at least one unit to display.")
        return

    origins, cell_w, cell_h, first_lyr = _compute_origins(rodb, q_rows, q_cols, gap_x, gap_y)

    # ── Scope — read from global Analysis Scope (sidebar) ───────────────────
    # Buildup: use whatever is active in the global scope bar (BU-01, BU-02 …)
    bu = st.session_state.get('buildup_filter_select', aoi.buildup_numbers)

    # Panel filter — read from the global Analysis Scope (scope_panel_sel).
    panel_filter = st.session_state.get('panel_filter_select', None)

    # Scope filter (buildup/side) then restrict to the selected units.
    side = st.session_state.get('scope_side_sel', ['Front', 'Back'])
    src = filter_aoi_cm(
        aoi.all_defects,
        tuple(sorted(bu)) if bu else (),
        tuple(sorted(side)),
    ).copy()

    # Apply panel filter from global scope.
    if panel_filter is not None and 'PANEL_ID' in src.columns:
        src = src[src['PANEL_ID'].isin(panel_filter)].copy()

    if 'UNIT_INDEX_Y' not in src.columns or 'UNIT_INDEX_X' not in src.columns:
        st.error("Cannot align defects: AOI data is missing UNIT_INDEX_X / UNIT_INDEX_Y columns.")
        return

    src['_ukey'] = list(zip(src['UNIT_INDEX_Y'].astype(int), src['UNIT_INDEX_X'].astype(int)))
    src = src[src['_ukey'].isin(set(sel_units))].copy()
    src.drop(columns=['_ukey'], inplace=True)

    if src.empty:
        st.info("No defects found for the selected units / scope filters.")
        _render_empty_layers(rodb, first_lyr, cell_w, cell_h)
        return

    if 'X_MM' not in src.columns or 'Y_MM' not in src.columns:
        st.error("Cannot align defects: AOI data is missing X_MM / Y_MM columns.")
        return

    # Unit placement angle — cumulative rotation from the step hierarchy (handles
    # cluster-level and unit-level rotation identically; 0 when unrotated).
    dom_angle = getattr(rodb.panel_layout, 'dominant_angle', 0.0) if (rodb and rodb.panel_layout) else 0.0

    pairs = list(zip(src['UNIT_INDEX_Y'].astype(int), src['UNIT_INDEX_X'].astype(int)))
    ox_arr = [origins.get(p, (0.0, 0.0))[0] for p in pairs]
    oy_arr = [origins.get(p, (0.0, 0.0))[1] for p in pairs]

    # ── Normalize defects to the COPPER bounding box ───────────────────────
    # Re-anchor from the unit corner to the copper corner so on-copper defects collapse
    # into [0, copper]; copper is drawn corner-to-corner (cell==copper → _design_anchor
    # corner-anchors it). The larger unit outline is drawn separately (white) around it.
    _cb = first_lyr.bounds if first_lyr else (0.0, 0.0, cell_w, cell_h)
    _copper_w = _cb[2] - _cb[0]
    _copper_h = _cb[3] - _cb[1]
    _margin_x = max(0.0, (cell_w - _copper_w) / 2.0)
    _margin_y = max(0.0, (cell_h - _copper_h) / 2.0)

    # Effective reference cell = the copper bbox; drives the canvas, outlines and anchor.
    ref_w, ref_h = _copper_w, _copper_h

    off_x = align_args.get('manual_offset_x', 0.0)
    off_y = align_args.get('manual_offset_y', 0.0)
    # Shift the unit-corner frame to the copper corner by removing the per-side margin.
    off_x -= _margin_x
    off_y -= _margin_y

    # Optional manual rotation override (rotates the entire view: CAD, defects, and canvas).
    with st.form("cm_rotation_form", border=False):
        manual_rot = st.number_input(
            "View rotation override (°)", min_value=0.0, max_value=360.0,
            value=0.0, step=0.5, format="%.1f", key='cm_rotation_deg',
            help="Rotate the entire view (CAD background, defects, and canvas dimensions). Useful if you want to look at the unit vertically instead of horizontally.",
        )
        st.form_submit_button("Apply rotation", use_container_width=True)

    try:
        ax, ay = _align_defects(
            tuple(src['X_MM'].values.tolist()),
            tuple(src['Y_MM'].values.tolist()),
            tuple(ox_arr), tuple(oy_arr),
            off_x, off_y,
        )
    except ValueError as e:
        st.error(f"Cannot align defects: {e}")
        return

    # Defects are aligned in the unit's native frame (translation only). Fault-site
    # grouping below also runs in this native frame so sites are rotation-invariant.
    cm_plot = src.copy()
    cm_plot['ALIGNED_X'] = list(ax)
    cm_plot['ALIGNED_Y'] = list(ay)

    cfg = OverlayConfig()
    cfg.color_mode     = st.session_state.get('color_mode_select', 'by_type')
    cfg.marker_style   = st.session_state.get('marker_style_select', 'dot')
    cfg.buildup_filter = bu
    cfg.defect_types   = st.session_state.get('defect_type_select', aoi.defect_types)
    cfg.side_filter    = 'Both'

    # ── Display orientation: defects are already in the panel frame (translation), so we
    #    only swap the displayed cell dims and rotate the reference DESIGN to match. ──
    theta = round(dom_angle) % 360
    svg_rot = (theta + manual_rot) % 360  # manual nudge sits on top of the auto angle
    disp_w, disp_h = _display_dims(ref_w, ref_h, theta)
    cfg.board_bounds = (-1.0, -1.0, disp_w + 1.0, disp_h + 1.0)

    fig = build_defect_only_figure(cm_plot, cfg)

    active_layer = _overlay_reference_layers(fig, rodb, svg_rot, theta, first_lyr, cfg,
                                             ref_w, ref_h)
    if active_layer is None:
        # No reference layers selected — still need to apply the base layout.
        _apply_layout(fig, cfg)
    # Copper boundary (green) traces the canvas extent (= copper bbox, where defects
    # normalize). The larger UNIT outline (white) is drawn margin-out on each side so the
    # dielectric border around the copper stays visible. Margins swap with the display
    # rotation, matching _display_dims.
    fig.add_shape(type="rect", x0=0, y0=0, x1=disp_w, y1=disp_h,
                  line=dict(color="rgba(0,180,80,0.5)", width=1.5),
                  fillcolor="rgba(0,0,0,0)", layer="below")
    _mdx, _mdy = (_margin_y, _margin_x) if theta in (90, 270) else (_margin_x, _margin_y)
    fig.add_shape(type="rect", x0=-_mdx, y0=-_mdy, x1=disp_w + _mdx, y1=disp_h + _mdy,
                  line=dict(color="rgba(255,255,255,0.85)", width=1.5),
                  fillcolor="rgba(0,0,0,0)", layer="below")

    _add_grid(fig, disp_w, disp_h)
    _panel_lbl = ", ".join(panel_filter) if panel_filter else None
    _add_dim_annotations(fig, disp_w, disp_h, active_layer, panel_label=_panel_lbl)

    n_def, n_units = len(cm_plot), len(sel_units)
    fig.update_layout(title=dict(
        text=f"{n_def} defects · {n_units} units · avg {n_def/max(n_units,1):.1f}/unit",
        font=dict(color="rgba(180,180,180,0.8)", size=12, family="monospace"),
        x=0.5, xanchor="center",
    ))

    # ── PNG export — two-step so kaleido never runs on a regular rerun ──────
    # Rasterizing this figure (multi-MB SVG layer images, scale=3) costs seconds;
    # generate only when the user asks, and keep the bytes until any figure-shaping
    # input changes (the signature below covers them all).
    _png_sig = (
        tuple(sorted(sel_units)),
        tuple(sorted(bu)) if bu else (),
        tuple(sorted(side)),
        tuple(sorted(panel_filter)) if panel_filter else (),
        float(manual_rot), float(off_x), float(off_y),
        cfg.color_mode, cfg.marker_style,
        bool(st.session_state.get('invert_polarity', False)),
        tuple(
            (n, bool(st.session_state.get(f"vis_{n}", False)),
             st.session_state.get(f"opacity_{n}"))
            for n in sorted(rodb.layers)
        ) if (rodb and rodb.layers) else (),
        len(cm_plot),
    )

    _export_col, _ = st.columns([1, 4])
    with _export_col:
        _png_ready = (st.session_state.get('_cm_png_sig') == _png_sig
                      and st.session_state.get('_cm_png_bytes') is not None)
        if not _png_ready:
            if st.button("📷 Prepare PNG", width="stretch", key="cm_prepare_png",
                         help="Render the current view to a PNG (a few seconds), then download."):
                try:
                    with st.spinner("Rendering PNG..."):
                        st.session_state['_cm_png_bytes'] = export_current_view(fig, fmt='png', scale=3)
                    st.session_state['_cm_png_sig'] = _png_sig
                    _png_ready = True
                except Exception:
                    st.session_state.pop('_cm_png_bytes', None)
                    st.session_state.pop('_cm_png_sig', None)
                    st.warning("PNG export failed — is kaleido installed?")
        if _png_ready:
            st.download_button("⬇️ Download PNG", data=st.session_state['_cm_png_bytes'],
                               file_name="commonality_unit.png", mime="image/png",
                               width="stretch", key="cm_download_png")

    st.plotly_chart(fig, width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})

    _render_metrics(sel_units, cm_plot)


def _render_empty_layers(rodb, first_lyr, cell_w, cell_h):
    """Show the reference design (no defects) when the scope filter matched nothing.
    Rotated to panel orientation, consistent with the defect/empty states."""
    if not (rodb and rodb.layers and first_lyr):
        return
    checked = [(n, l) for n, l in rodb.layers.items() if st.session_state.get(f"vis_{n}", False)]
    if not checked:
        st.caption("☝️ Select a layer in the sidebar to view the design.")
        return
    # Anchor + size the design from the UNROTATED cell dims (before the swap below).
    _ref_shift, _ref_w, _ref_h = _design_anchor(first_lyr.bounds, cell_w, cell_h)
    is_multi = len(checked) > 1
    _rot = float(round(getattr(rodb.panel_layout, 'dominant_angle', 0.0)) % 360) if rodb.panel_layout else 0.0
    _swap = _rot in (90.0, 270.0)
    if _swap:
        cell_w, cell_h = cell_h, cell_w
    fig = go.Figure()
    _place_pairs(fig, sorted(checked, key=_layer_sort_key), _ref_shift, _rot, _rot,
                 is_multi, _layer_color_map(rodb), _ref_w, _ref_h)
    _add_dim_annotations(fig, cell_w, cell_h, " + ".join(n for n, _ in checked))
    fig.update_layout(
        xaxis=dict(range=[-1, cell_w + 1], scaleanchor='y', scaleratio=1,
                   showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(range=[-1, cell_h + 1], showgrid=False, zeroline=False, showticklabels=False),
        plot_bgcolor='#000000', paper_bgcolor='#000000', font=dict(color='#cccccc'),
        margin=dict(l=0, r=0, t=36, b=0), height=800,
    )
    st.plotly_chart(fig, width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@st.fragment
def render_unit_commonality(parsed, aoi, align_args) -> None:
    st.markdown("### 🗺️ Commonality — Defect Superposition")
    st.caption("Normalise each selected unit's defects into local coordinates and overlay on a single reference unit.")

    rodb = st.session_state.get('rendered_odb')
    has_aoi_cm = (
        aoi and aoi.has_data
        and 'UNIT_INDEX_X' in aoi.all_defects.columns
        and 'UNIT_INDEX_Y' in aoi.all_defects.columns
    )

    if not rodb and not has_aoi_cm:
        st.info("Upload a TGZ design file or AOI defect data to use this view.")
        return

    # Warm the per-layer SVG cache in the background (once per board) so the first click
    # on any layer is instant. Non-blocking; safe to fail.
    if rodb and rodb.layers:
        try:
            _angle = getattr(rodb.panel_layout, 'dominant_angle', 0.0) if rodb.panel_layout else 0.0
            prewarm_layer_urls(rodb, float(round(_angle) % 360))
        except Exception:
            pass

    na_checked = _render_sidebar_controls(rodb)

    if not has_aoi_cm:
        _render_empty_state(rodb, na_checked)
    else:
        _render_defect_state(rodb, aoi, align_args)
