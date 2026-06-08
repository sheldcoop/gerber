import streamlit as st
import numpy as np
import plotly.graph_objects as go
from typing import Any, Tuple, List

from core.data_utils import (
    compute_cm_geometry, filter_aoi_cm, _align_defects, _compute_pad_fingerprint,
    _SEV_DOT_SCALE, _SEV_LABEL, _SEV_COLOR,
)
from core.svg_utils import build_rotated_svg_url
from alignment import calculate_geometry, INTER_UNIT_GAP
from visualizer import OverlayConfig, build_defect_only_figure, _apply_layout
from export import export_current_view

_LAYER_Z = {
    'drill': 0, 'other': 1, 'paste': 2,
    'soldermask': 3, 'silkscreen': 4, 'outline': 5, 'copper': 6
}
_LAYER_OPACITY_SINGLE = {'copper': 0.95, 'drill': 0.55, 'other': 0.60}
_LAYER_OPACITY_MULTI  = {'copper': 0.90, 'drill': 0.45, 'other': 0.50}


def _layer_sort_key(name_lyr_pair: Tuple[str, Any]) -> int:
    return _LAYER_Z.get(name_lyr_pair[1].layer_type, 1)


def _layer_opacity(layer_name: str, lyr_type: str, multi: bool) -> float:
    slider_val = st.session_state.get(f"opacity_{layer_name}")
    if slider_val is not None:
        return float(slider_val)
    d = _LAYER_OPACITY_MULTI if multi else _LAYER_OPACITY_SINGLE
    return d.get(lyr_type, 0.70 if multi else 0.85)


def _svg_url(lyr_obj: Any, rot_deg: float, is_multi: bool) -> str:
    """Reference-layer SVG url honouring the invert-polarity toggle."""
    return build_rotated_svg_url(
        lyr_obj, rot_deg, is_multi,
        invert=st.session_state.get('invert_polarity', False),
    )


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

    _ref_b  = _ref_lyr.bounds
    _ref_sx = -_ref_b[0]
    _ref_sy = -_ref_b[1]
    _is_multi = len(na_checked) > 1
    _sorted = sorted(na_checked, key=_layer_sort_key)

    _unit_angle = getattr(rodb_cm_check.panel_layout, 'dominant_angle', 0.0) if rodb_cm_check.panel_layout else 0.0
    _rot_deg = float(round(_unit_angle) % 360)
    # At 90/270 the unit footprint is rotated — swap canvas dims once (not per layer).
    if _rot_deg in (90.0, 270.0):
        cw, ch = ch, cw

    fig = go.Figure()
    for _n, _l in _sorted:
        _url = _svg_url(_l, _rot_deg, _is_multi)
        _b = _l.bounds
        _im_w = _b[2] - _b[0]
        _im_h = _b[3] - _b[1]
        if _rot_deg in (90.0, 270.0):
            _sx, _sy, _x, _y = _im_h, _im_w, 0.0, _im_w
        else:
            _sx, _sy, _x, _y = _im_w, _im_h, _b[0] + _ref_sx, _b[3] + _ref_sy
        fig.add_layout_image(dict(
            source=_url, xref="x", yref="y",
            x=_x, y=_y, sizex=_sx, sizey=_sy,
            sizing="stretch", layer="below",
            opacity=_layer_opacity(_n, _l.layer_type, _is_multi),
        ))

    _lbl = " + ".join(n for n, _ in na_checked)
    _add_dim_annotations(fig, cw, ch, _lbl)
    fig.update_layout(
        xaxis=dict(range=[-1, cw + 1], scaleanchor='y', scaleratio=1,
                   showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(range=[-1, ch + 1], showgrid=False,
                   zeroline=False, showticklabels=False),
        plot_bgcolor='#000000', paper_bgcolor='#000000',
        font=dict(color='#cccccc'),
        margin=dict(l=0, r=0, t=36, b=0), height=600,
    )
    st.plotly_chart(fig, width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})


# ---------------------------------------------------------------------------
# Shared annotation helper (W/H labels + optional layer label)
# ---------------------------------------------------------------------------

def _add_dim_annotations(fig: go.Figure, cw: float, ch: float, layer_label: str = None) -> None:
    fig.add_annotation(
        x=cw / 2, y=-ch * 0.045, text=f"W: {cw:.2f} mm", showarrow=False,
        font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
        xref="x", yref="y",
    )
    fig.add_annotation(
        x=-cw * 0.045, y=ch / 2, text=f"H: {ch:.2f} mm", showarrow=False, textangle=-90,
        font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
        xref="x", yref="y",
    )
    if layer_label:
        fig.add_annotation(
            x=cw / 2, y=ch + ch * 0.04, text=f"Layer: {layer_label}",
            showarrow=False, xanchor="center", yanchor="bottom",
            font=dict(color="rgba(0,220,130,0.95)", size=12, family="monospace"),
            xref="x", yref="y",
        )


# ---------------------------------------------------------------------------
# Unit selection (multiselect + quadrant quick-select)
# ---------------------------------------------------------------------------

def _select_units(rodb, aoi):
    """Render the unit multiselect + Q1-Q4 buttons. Returns (sel_units, all_pairs,
    q_rows, q_cols, gap_x, gap_y)."""
    q_rows = int(st.session_state.get('quad_rows_input', 6))
    q_cols = int(st.session_state.get('quad_cols_input', 6))
    gap_x  = float(st.session_state.get('dyn_gap_x_input', 5.0))
    gap_y  = float(st.session_state.get('dyn_gap_y_input', 3.5))

    if rodb and rodb.panel_layout:
        _rp  = rodb.panel_layout.unit_positions
        _uxs = sorted(set(round(x, 2) for x, _ in _rp))
        _uys = sorted(set(round(y, 2) for _, y in _rp))
        all_pairs = [(ri, ci) for ri in range(len(_uys)) for ci in range(len(_uxs))]
        q_rows = max(1, len(_uys) // 2)
        q_cols = max(1, len(_uxs) // 2)
    else:
        _aup = (
            aoi.all_defects[['UNIT_INDEX_Y', 'UNIT_INDEX_X']]
            .dropna().drop_duplicates()
            .sort_values(['UNIT_INDEX_Y', 'UNIT_INDEX_X'])
            .values.tolist()
        )
        all_pairs = [(int(r), int(c)) for r, c in _aup]
    all_labels = [f"({r},{c})" for r, c in all_pairs]

    def _quad(r, c):
        qr, qc = r // q_rows, c // q_cols
        return {(0, 0): 'Q2', (0, 1): 'Q3', (1, 0): 'Q1', (1, 1): 'Q4'}.get((qr, qc), 'Other')

    _q_lbls = {q: [l for (r, c), l in zip(all_pairs, all_labels) if _quad(r, c) == q]
               for q in ('Q1', 'Q2', 'Q3', 'Q4')}

    if 'cm_multiselect' not in st.session_state:
        st.session_state['cm_multiselect'] = all_labels

    def _set(labels):
        def cb():
            st.session_state['cm_multiselect'] = [l for l in labels if l in all_labels]
        return cb

    _cols = st.columns(6, gap="small")
    _cols[0].button("ALL",   key="cm_all",   on_click=_set(all_labels),     width="stretch", type="primary")
    _cols[1].button("Q1",    key="cm_q1",    on_click=_set(_q_lbls['Q1']),  width="stretch")
    _cols[2].button("Q2",    key="cm_q2",    on_click=_set(_q_lbls['Q2']),  width="stretch")
    _cols[3].button("Q3",    key="cm_q3",    on_click=_set(_q_lbls['Q3']),  width="stretch")
    _cols[4].button("Q4",    key="cm_q4",    on_click=_set(_q_lbls['Q4']),  width="stretch")
    _cols[5].button("Clear", key="cm_clear", on_click=_set([]),             width="stretch")

    _cur = [l for l in st.session_state.get('cm_multiselect', []) if l in all_labels]
    if _cur != st.session_state.get('cm_multiselect'):
        st.session_state['cm_multiselect'] = _cur

    _sel_labels = st.multiselect(
        "Selected units (row, col)", options=all_labels, key='cm_multiselect',
        help="Choose which units' defects to superimpose. Use the quick-select buttons above for bulk selection.",
    )

    sel_units = []
    for _lbl in _sel_labels:
        try:
            _r, _c = _lbl.strip('()').split(',')
            sel_units.append((int(_r.strip()), int(_c.strip())))
        except Exception:
            pass

    st.caption(f"**{len(sel_units)}** / {len(all_pairs)} units selected")
    st.divider()
    return sel_units, all_pairs, q_rows, q_cols, gap_x, gap_y


# ---------------------------------------------------------------------------
# Origin geometry for the reference unit cell
# ---------------------------------------------------------------------------

def _compute_origins(rodb, q_rows, q_cols, gap_x, gap_y):
    """Returns (origins_dict, cell_w, cell_h, first_layer_or_None)."""
    if rodb and rodb.panel_layout and rodb.layers:
        first_lyr = next(
            (l for l in rodb.layers.values() if l.layer_type != 'drill'),
            next(iter(rodb.layers.values()))
        )
        origins, cw, ch = compute_cm_geometry(
            unit_positions=tuple(rodb.panel_layout.unit_positions),
            first_layer_bounds=tuple(first_lyr.bounds),
            unit_bounds=rodb.panel_layout.unit_bounds,
        )
        return origins, cw, ch, first_lyr

    # No TGZ: synthesise origins from the geometry context.
    ctx = calculate_geometry(q_rows, q_cols, gap_x, gap_y)
    _pos: list = []
    for _qox, _qoy in ctx.quadrant_origins.values():
        for _rr in range(q_rows):
            for _cc in range(q_cols):
                _pos.append((
                    _qox + INTER_UNIT_GAP + _cc * ctx.stride_x,
                    _qoy + INTER_UNIT_GAP + _rr * ctx.stride_y,
                ))
    _xs = sorted(set(round(x, 2) for x, _ in _pos))
    _ys = sorted(set(round(y, 2) for _, y in _pos))
    origins = {(ri, ci): (_xs[ci], _ys[ri])
               for ri in range(len(_ys)) for ci in range(len(_xs))}
    return origins, ctx.cell_width, ctx.cell_height, None


# ---------------------------------------------------------------------------
# Reference design layer overlay
# ---------------------------------------------------------------------------

def _overlay_reference_layers(fig, rodb, manual_rot, first_lyr, cfg):
    """Draw checked reference design layers under the defect cloud. Returns the
    active (top-most) layer name, or None."""
    if not (rodb and rodb.layers and first_lyr):
        return None

    cam_layers = [n for n in rodb.layers if st.session_state.get(f"vis_{n}", False)]
    is_multi = len(cam_layers) > 1
    pairs = [(ln, rodb.layers[ln]) for ln in cam_layers if rodb.layers.get(ln)]
    pairs.sort(key=_layer_sort_key)
    if not pairs:
        return None
    active = pairs[-1][0]

    _ref_b = first_lyr.bounds
    _sx = -_ref_b[0]
    _sy = -_ref_b[1]
    _swap = round(manual_rot) % 360 in (90, 270)

    for _ln, _lyr in pairs:
        _url = _svg_url(_lyr, manual_rot, is_multi)
        _b = _lyr.bounds
        _im_w = _b[2] - _b[0]
        _im_h = _b[3] - _b[1]
        if _swap:
            _szx, _szy, _x, _y = _im_h, _im_w, 0.0, _im_w
        else:
            _szx, _szy, _x, _y = _im_w, _im_h, _b[0] + _sx, _b[3] + _sy
        fig.add_layout_image(dict(
            source=_url, xref="x", yref="y",
            x=_x, y=_y, sizex=_szx, sizey=_szy,
            sizing="stretch", layer="below",
            opacity=_layer_opacity(_ln, _lyr.layer_type, is_multi),
        ))
    _apply_layout(fig, cfg)
    return active


# ---------------------------------------------------------------------------
# Fingerprint figure
# ---------------------------------------------------------------------------

def _build_fingerprint_figure(fp_df, n_sel_units):
    fig = go.Figure()
    for _sev in (3, 2, 1, 0):
        rows = fp_df[fp_df['severity'] == _sev]
        if rows.empty:
            continue
        sizes = (rows['unit_count'].clip(upper=n_sel_units)
                 .apply(lambda n: _SEV_DOT_SCALE[_sev] + n * 1.2))
        fig.add_trace(go.Scatter(
            x=rows['cx'], y=rows['cy'], mode='markers', name=_SEV_LABEL[_sev],
            marker=dict(color=_SEV_COLOR[_sev], size=sizes, opacity=0.82,
                        line=dict(color='rgba(0,0,0,0.4)', width=0.8)),
            customdata=rows[['unit_count', 'unit_pct', 'top_verif', 'all_verif',
                             'top_type', 'buildup', 'defect_count']].values,
            hovertemplate=(
                "<b>Verification: %{customdata[2]}</b><br>"
                "All codes at this site: %{customdata[3]}<br>"
                "Defect type: %{customdata[4]}<br>"
                "Units hit: <b>%{customdata[0]}</b> (%{customdata[1]}%)<br>"
                "Total defects at site: %{customdata[6]}<br>"
                "Buildup: %{customdata[5]}<br>"
                "X: %{x:.2f} mm  Y: %{y:.2f} mm"
                "<extra></extra>"
            ),
        ))
    return fig


# ---------------------------------------------------------------------------
# Metrics + fault-site table
# ---------------------------------------------------------------------------

def _render_metrics(fp_df, sel_units, cm_plot):
    c1, c2, c3 = st.columns(3)
    if not fp_df.empty:
        systemic = int((fp_df['unit_pct'] >= 50.0).sum())
        worst = fp_df.iloc[0]
        crit_sys = int(((fp_df['severity'] == 3) & (fp_df['unit_pct'] >= 50.0)).sum())
        c1.metric("Systemic Fault Sites (≥ 50 % units)", systemic,
                  help="Fault sites where ≥ 50 % of selected units had a defect. These are process faults, not random.")
        c2.metric("Worst Site Hit Rate", f"{worst['unit_pct']:.0f}% — {worst['top_verif']}",
                  help="The fault site with the highest unit hit % and the top verification code driving it.")
        c3.metric("Critical + Systemic", crit_sys,
                  delta=f"of {len(fp_df)} total fault sites", delta_color="inverse",
                  help="Critical-severity fault sites also failing on ≥ 50 % of units — the highest-priority items to fix.")
    else:
        c1.metric("Units Selected", len(sel_units))
        c2.metric("Defects Shown", len(cm_plot))
        c3.metric("Avg / Unit", f"{len(cm_plot)/max(len(sel_units),1):.1f}")


def _fault_site_table(fp_df):
    if fp_df.empty:
        return
    st.divider()
    st.markdown("#### 🔬 Fault Site Recurrence Fingerprint")
    st.caption(
        "Each row is a distinct fault site (defects snapped to 0.5 mm grid). "
        "**Unit hit %** = how many of the selected units had a defect here. "
        "A site at 100 % is a **systemic process fault** — it fails on every unit, every time."
    )

    systemic = fp_df[fp_df['unit_pct'] >= 80.0]
    if not systemic.empty:
        crit_sys = systemic[systemic['severity'] == 3]
        if not crit_sys.empty:
            st.error(
                f"🚨 **{len(crit_sys)} Critical fault site(s) are failing on ≥ 80 % of units.** "
                "This is a systemic process fault — not random defects."
            )
        else:
            st.warning(f"⚠️ **{len(systemic)} fault site(s) are failing on ≥ 80 % of units.**")

    disp = fp_df.copy()
    disp.insert(0, 'Rank', range(1, len(disp) + 1))
    disp = disp.rename(columns={
        'cx': 'X (mm)', 'cy': 'Y (mm)', 'unit_count': 'Units Hit', 'unit_pct': 'Hit %',
        'severity_label': 'Severity', 'top_verif': 'Top Verification',
        'all_verif': 'All Verif. Codes', 'top_type': 'Top Defect Type',
        'buildup': 'Buildup(s)', 'defect_count': 'Total Defects',
    }).drop(columns=['severity'])

    def _colour_sev(val):
        c = {'Critical': '#FF3B3B', 'High': '#FF9900',
             'Medium': '#FFD700', 'Low': '#66BB6A'}.get(val, '')
        return f'color: {c}; font-weight: bold' if c else ''

    styled = (
        disp.style
        .applymap(_colour_sev, subset=['Severity'])
        .background_gradient(subset=['Hit %'], cmap='Reds', vmin=0, vmax=100)
        .format({'X (mm)': '{:.2f}', 'Y (mm)': '{:.2f}', 'Hit %': '{:.1f}'})
    )
    st.dataframe(styled, use_container_width=True, height=320)


# ---------------------------------------------------------------------------
# Defect state — the interactive superposition view
# ---------------------------------------------------------------------------

def _render_defect_state(rodb, aoi, align_args, get_svg_url):
    sel_units, all_pairs, q_rows, q_cols, gap_x, gap_y = _select_units(rodb, aoi)
    if not sel_units:
        st.info("Select at least one unit to display.")
        return

    origins, cell_w, cell_h, first_lyr = _compute_origins(rodb, q_rows, q_cols, gap_x, gap_y)

    # Scope filter (buildup/side) then restrict to the selected units.
    bu   = st.session_state.get('buildup_filter_select', aoi.buildup_numbers)
    side = st.session_state.get('scope_side_sel', ['Front', 'Back'])
    src = filter_aoi_cm(
        aoi.all_defects,
        tuple(sorted(bu)) if bu else (),
        tuple(sorted(side)),
    ).copy()

    if 'UNIT_INDEX_Y' not in src.columns or 'UNIT_INDEX_X' not in src.columns:
        st.error("Cannot align defects: AOI data is missing UNIT_INDEX_X / UNIT_INDEX_Y columns.")
        return

    src['_ukey'] = list(zip(src['UNIT_INDEX_Y'].astype(int), src['UNIT_INDEX_X'].astype(int)))
    src = src[src['_ukey'].isin(set(sel_units))].copy()
    src.drop(columns=['_ukey'], inplace=True)

    if src.empty:
        st.info("No defects found for the selected units / scope filters.")
        _render_empty_layers(rodb, first_lyr, cell_w, cell_h, get_svg_url)
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
    off_x = align_args.get('manual_offset_x', 0.0)
    off_y = align_args.get('manual_offset_y', 0.0)

    # Optional manual background nudge (rotates the reference SVG only).
    with st.form("cm_rotation_form", border=False):
        manual_rot = st.number_input(
            "Background rotation (°)", min_value=0.0, max_value=360.0,
            value=0.0, step=0.5, format="%.1f", key='cm_rotation_deg',
            help="Fine-tune the CAD background orientation. Defects are auto-aligned to the unit's detected angle; this only nudges the background image.",
        )
        st.form_submit_button("Apply rotation", use_container_width=True)

    try:
        ax, ay = _align_defects(
            tuple(src['X_MM'].values.tolist()),
            tuple(src['Y_MM'].values.tolist()),
            tuple(ox_arr), tuple(oy_arr),
            off_x, off_y, unit_angle=dom_angle,
        )
    except ValueError as e:
        st.error(f"Cannot align defects: {e}")
        return

    cm_plot = src.copy()
    cm_plot['ALIGNED_X'] = list(ax)
    cm_plot['ALIGNED_Y'] = list(ay)

    cfg = OverlayConfig()
    cfg.board_bounds   = (-1.0, -1.0, cell_w + 1.0, cell_h + 1.0)
    cfg.color_mode     = st.session_state.get('color_mode_select', 'by_type')
    cfg.marker_style   = st.session_state.get('marker_style_select', 'dot')
    cfg.buildup_filter = bu
    cfg.defect_types   = st.session_state.get('defect_type_select', aoi.defect_types)
    cfg.side_filter    = 'Both'

    fp_mode = st.toggle(
        "🔬 Fault Site Fingerprint Mode", value=False, key="cm_fingerprint_toggle",
        help=("Replace raw dots with one marker per recurring fault site. "
              "Size = how many units were hit. "
              "Colour = worst defect severity (red=Critical, orange=High, yellow=Medium, green=Low). "
              "Hover to see the top verification code (e.g. CU22, SH, OP)."),
    )

    fp_df = _compute_pad_fingerprint(
        ax_tuple=tuple(cm_plot['ALIGNED_X'].tolist()),
        ay_tuple=tuple(cm_plot['ALIGNED_Y'].tolist()),
        defect_types=tuple(cm_plot['DEFECT_TYPE'].tolist() if 'DEFECT_TYPE' in cm_plot.columns
                           else ['unknown'] * len(cm_plot)),
        unit_keys=tuple(zip(cm_plot['UNIT_INDEX_Y'].astype(int).tolist(),
                            cm_plot['UNIT_INDEX_X'].astype(int).tolist())),
        buildup_vals=tuple(cm_plot['BUILDUP'].tolist() if 'BUILDUP' in cm_plot.columns else []),
        verification_vals=tuple(cm_plot['VERIFICATION'].tolist() if 'VERIFICATION' in cm_plot.columns else []),
        verif_severity_map=tuple(st.session_state.get('verif_severity_map', {}).items()),
    )

    if fp_mode and not fp_df.empty:
        fig = _build_fingerprint_figure(fp_df, len(sel_units))
    else:
        fig = build_defect_only_figure(cm_plot, cfg)

    active_layer = _overlay_reference_layers(fig, rodb, manual_rot, first_lyr, cfg)
    if active_layer is None:
        # No reference layers — draw the unit cell outline and apply layout.
        fig.add_shape(type="rect", x0=0, y0=0, x1=cell_w, y1=cell_h,
                      line=dict(color="rgba(0,180,80,0.5)", width=1.5),
                      fillcolor="rgba(0,0,0,0)", layer="below")
        _apply_layout(fig, cfg)

    _add_grid(fig, cell_w, cell_h)
    _add_dim_annotations(fig, cell_w, cell_h, active_layer)

    _show_heatmap = st.toggle("🌡️ Density Heatmap", value=False,
                              help="Overlay a 2D defect density heatmap instead of individual dots",
                              key="cm_heatmap_toggle")
    if _show_heatmap and len(cm_plot) >= 3:
        _add_density_heatmap(fig, cm_plot, cell_w, cell_h)

    n_def, n_units = len(cm_plot), len(sel_units)
    fig.update_layout(title=dict(
        text=f"{n_def} defects · {n_units} units · avg {n_def/max(n_units,1):.1f}/unit",
        font=dict(color="rgba(180,180,180,0.8)", size=12, family="monospace"),
        x=0.5, xanchor="center",
    ))

    _export_col, _ = st.columns([1, 4])
    with _export_col:
        try:
            _png = export_current_view(fig, fmt='png', scale=3)
            st.download_button("📷 Export PNG", data=_png, file_name="commonality_unit.png",
                               mime="image/png", width="stretch")
        except Exception:
            st.button("📷 Export PNG (kaleido required)", disabled=True, width="stretch")

    st.plotly_chart(fig, width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})

    _render_metrics(fp_df, sel_units, cm_plot)
    _fault_site_table(fp_df)


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


def _render_empty_layers(rodb, first_lyr, cell_w, cell_h, get_svg_url):
    """Show the reference design (no defects) when the scope filter matched nothing."""
    if not (rodb and rodb.layers and first_lyr):
        return
    checked = [(n, l) for n, l in rodb.layers.items() if st.session_state.get(f"vis_{n}", False)]
    if not checked:
        st.caption("☝️ Select a layer in the sidebar to view the design.")
        return
    _ref_b = first_lyr.bounds
    _sx, _sy = -_ref_b[0], -_ref_b[1]
    is_multi = len(checked) > 1
    fig = go.Figure()
    for _n, _l in sorted(checked, key=_layer_sort_key):
        _b = _l.bounds
        fig.add_layout_image(dict(
            source=get_svg_url(_l), xref="x", yref="y",
            x=_b[0] + _sx, y=_b[3] + _sy,
            sizex=_b[2] - _b[0], sizey=_b[3] - _b[1],
            sizing="stretch", layer="below",
            opacity=_layer_opacity(_n, _l.layer_type, is_multi),
        ))
    _add_dim_annotations(fig, cell_w, cell_h, " + ".join(n for n, _ in checked))
    fig.update_layout(
        xaxis=dict(range=[-1, cell_w + 1], scaleanchor='y', scaleratio=1,
                   showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(range=[-1, cell_h + 1], showgrid=False, zeroline=False, showticklabels=False),
        plot_bgcolor='#000000', paper_bgcolor='#000000', font=dict(color='#cccccc'),
        margin=dict(l=0, r=0, t=36, b=0), height=600,
    )
    st.plotly_chart(fig, width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@st.fragment
def render_unit_commonality(parsed, aoi, align_args, get_svg_url):
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

    na_checked = _render_sidebar_controls(rodb)

    if not has_aoi_cm:
        _render_empty_state(rodb, na_checked)
    else:
        _render_defect_state(rodb, aoi, align_args, get_svg_url)
