import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from alignment import get_panel_quadrant_bounds, FRAME_WIDTH, FRAME_HEIGHT
from visualizer import OverlayConfig
from core.data_utils import compute_cm_geometry

def render_panel_heatmap(parsed, aoi, align_args):
    st.markdown("### 🔥 Panel Defect Heatmap")
    st.caption("Overlay a 2D density contour map of defects across the entire panel to spot systemic factory flaws.")

    if aoi and aoi.has_data:
        hm_df = aoi.all_defects.copy()
        _p_off_x = align_args.get('manual_offset_x', 0.0)
        _p_off_y = align_args.get('manual_offset_y', 0.0)
        if 'X_MM' not in hm_df.columns and 'X' in hm_df.columns:
            hm_df['ALIGNED_X'] = hm_df['X'] / 1000.0 + _p_off_x
            hm_df['ALIGNED_Y'] = hm_df['Y'] / 1000.0 + _p_off_y
        else:
            hm_df['ALIGNED_X'] = (hm_df['X_MM'] if 'X_MM' in hm_df.columns else 0.0) + _p_off_x
            hm_df['ALIGNED_Y'] = (hm_df['Y_MM'] if 'Y_MM' in hm_df.columns else 0.0) + _p_off_y

        if align_args.get('flip_y', False) and not hm_df.empty:
            hm_df['ALIGNED_Y'] = hm_df['ALIGNED_Y'].max() - hm_df['ALIGNED_Y']

        hm_config = OverlayConfig(min_feature_size=0.1)

        quad_bounds = get_panel_quadrant_bounds(
            st.session_state.get('quad_rows_input', 6),
            st.session_state.get('quad_cols_input', 6),
            dyn_gap_x=st.session_state.get('dyn_gap_x_input', 5.0),
            dyn_gap_y=st.session_state.get('dyn_gap_y_input', 3.5),
        )

        _rp_for_bounds = st.session_state.get('rendered_odb')
        if _rp_for_bounds and _rp_for_bounds.panel_layout:
            _vpw = _rp_for_bounds.panel_layout.panel_width
            _vph = _rp_for_bounds.panel_layout.panel_height
            hm_config.board_bounds = (-10, -10, _vpw + 10, _vph + 10)
        else:
            ax1, ay1, ax2, ay2 = quad_bounds['frame']
            hm_config.board_bounds = (ax1 - 10, ay1 - 10, ax2 + 10, ay2 + 10)

        hm_config.color_mode    = st.session_state.get('color_mode_select', 'by_type')
        hm_config.marker_style  = st.session_state.get('marker_style_select', 'dot')
        hm_config.defect_types  = st.session_state.get('defect_type_select', aoi.defect_types)
        hm_config.buildup_filter = st.session_state.get('buildup_filter_select', aoi.buildup_numbers)
        side_active = st.session_state.get('side_cap_select', 'All')
        hm_config.side_filter   = 'Both' if side_active == 'All' else side_active

        # ── Panel Source Filter — one toggle per unique panel ─────────────
        _panel_col = 'PANEL_ID' if 'PANEL_ID' in hm_df.columns else 'SOURCE_FILE'
        _all_sources = sorted(hm_df[_panel_col].unique().tolist()) if _panel_col in hm_df.columns else []
        if _all_sources:
            st.caption(f"**{len(_all_sources)} panel{'s' if len(_all_sources) != 1 else ''} loaded** — flip a toggle to exclude one panel from the analysis:")
            _tog_cols = st.columns(min(len(_all_sources), 8))
            _sel_sources = []
            for _pi, _src in enumerate(_all_sources):
                _short = f"P{_pi+1}"
                _included = _tog_cols[_pi % len(_tog_cols)].toggle(
                    _short, value=True,
                    key=f"panel_tog_{_pi}",
                    help=_src,
                )
                if _included:
                    _sel_sources.append(_src)
            if _sel_sources:
                hm_df = hm_df[hm_df[_panel_col].isin(_sel_sources)]
        else:
            _sel_sources = []

        # ── View mode toggle ───────────────────────────────────────────────
        _hm_mode = st.radio(
            "Heatmap Mode",
            ["🌡️ Density Contour", "📊 Unit Grid Count"],
            horizontal=True,
            key="hm_mode_radio",
            help=(
                "**Density Contour** — continuous spatial density of individual defect coordinates (X_MM/Y_MM). "
                "**Unit Grid Count** — each cell = one unit on the panel, coloured by total defect count. "
                "Use Grid Count to spot machine/conveyor edge effects; "
                "use Density Contour to see exact defect clusters within the panel area."
            ),
        )

        if _rp_for_bounds and _rp_for_bounds.panel_layout:
            frame_bx1, frame_by1 = 0.0, 0.0
            frame_bx2 = _rp_for_bounds.panel_layout.panel_width
            frame_by2 = _rp_for_bounds.panel_layout.panel_height
        else:
            frame_bx1, frame_by1, frame_bx2, frame_by2 = quad_bounds['frame']

        # ── Branch 1: Density Contour (existing) ───────────────────────────
        if _hm_mode == "🌡️ Density Contour":
            from visualizer import build_heatmap_figure
            hm_fig = build_heatmap_figure(hm_df, hm_config)

            hm_fig.add_shape(
                type="rect", x0=frame_bx1 - 8, y0=frame_by1 - 8,
                x1=frame_bx2 + 8, y1=frame_by2 + 8,
                fillcolor="#2B3A2B", line=dict(color="#1a2a1a", width=1), layer="below",
            )
            hm_fig.add_shape(
                type="rect", x0=frame_bx1, y0=frame_by1,
                x1=frame_bx2, y1=frame_by2,
                fillcolor="rgba(184,115,51,0.18)", line=dict(color="#C87533", width=3), layer="below",
            )

            hm_col1, hm_col2 = st.columns(2)
            hm_bin_opacity = hm_col1.slider(
                "Bin Layer Opacity", 0.1, 1.0, 0.85, 0.05, key="hm_opac",
                help="Opacity of the solid binned count layer (the precise layer).",
            )
            hm_kde_opacity = hm_col2.slider(
                "KDE Envelope Opacity", 0.0, 1.0, 0.5, 0.05, key="hm_kde_opac",
                help="Opacity of the smooth KDE overlay. Set to 0 to hide it.",
            )
            if len(hm_fig.data) >= 1:
                hm_fig.data[0].opacity = hm_bin_opacity
            if len(hm_fig.data) >= 2:
                hm_fig.data[1].opacity = hm_kde_opacity

            st.plotly_chart(hm_fig, width='stretch',
                            config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})

        # ── Branch 2: Unit Grid Count (true-scale, repeatability metric) ───
        else:
            import numpy as _np_hm

            _has_idx = 'UNIT_INDEX_X' in hm_df.columns and 'UNIT_INDEX_Y' in hm_df.columns
            if not _has_idx:
                st.warning("Unit index columns (UNIT_INDEX_X / UNIT_INDEX_Y) not found in the AOI data.")
            else:
                _n_panels_total = hm_df[_panel_col].nunique() if _panel_col in hm_df.columns else 1

                # ── Metric selector ────────────────────────────────────────
                # Repeatability % only makes sense with >1 panel
                _metric_options = ["Repeatability %", "Raw Count"] if _n_panels_total > 1 else ["Raw Count"]
                _grid_metric = st.radio(
                    "Metric",
                    _metric_options,
                    horizontal=True,
                    key="grid_metric_sel",
                    help=(
                        "**Repeatability %** — % of panels where this unit had ≥1 defect. "
                        "High = systematic machine fault. Low = one-off fluke. "
                        "**Raw Count** — total defects summed across all selected panels."
                        + ("" if _n_panels_total > 1 else
                           "\n\n*Repeatability % requires more than 1 panel.*")
                    ),
                )

                # ── Verification filter ────────────────────────────────────
                if 'VERIFICATION' in hm_df.columns:
                    _all_verif = sorted(hm_df['VERIFICATION'].dropna().unique().tolist())
                    _sel_verif = st.multiselect(
                        "Filter by verification code",
                        options=_all_verif,
                        default=_all_verif,
                        key="hm_verif_filter",
                        help="Only defects with these verification codes count toward repeatability and raw count.",
                    )
                    if _sel_verif:
                        hm_df = hm_df[hm_df['VERIFICATION'].isin(_sel_verif)]

                _max_col = int(hm_df['UNIT_INDEX_X'].max())
                _max_row = int(hm_df['UNIT_INDEX_Y'].max())
                _n_cols  = _max_col + 1
                _n_rows  = _max_row + 1

                # ── Compute the chosen metric per unit cell ────────────────
                _cnt_raw = (hm_df.groupby(['UNIT_INDEX_X', 'UNIT_INDEX_Y'])
                            .size().reset_index(name='N'))

                _Z = _np_hm.zeros((_n_rows, _n_cols), dtype=float)
                _Z_raw = _np_hm.zeros((_n_rows, _n_cols), dtype=float)

                if _grid_metric == "Repeatability %" and _panel_col in hm_df.columns:
                    _rep = (hm_df.groupby(['UNIT_INDEX_X', 'UNIT_INDEX_Y'])[_panel_col]
                            .nunique().reset_index(name='N_P'))
                    for _, _r in _rep.iterrows():
                        _Z[int(_r.UNIT_INDEX_Y), int(_r.UNIT_INDEX_X)] = (
                            _r.N_P / _n_panels_total * 100.0
                        )
                    _cb_title = "Repeatability %"
                    _label_suffix = "%"
                else:
                    for _, _r in _cnt_raw.iterrows():
                        _Z[int(_r.UNIT_INDEX_Y), int(_r.UNIT_INDEX_X)] = _r.N
                    _cb_title = "Defect Count"
                    _label_suffix = ""

                for _, _r in _cnt_raw.iterrows():
                    _Z_raw[int(_r.UNIT_INDEX_Y), int(_r.UNIT_INDEX_X)] = _r.N

                # ── Map unit indices → true mm panel positions ─────────────
                _x_vals  = list(range(_n_cols))
                _y_vals  = list(range(_n_rows))
                _x_label, _y_label = "Unit Column", "Unit Row"
                _cw, _ch = 1.0, 1.0
                _use_mm  = False

                if _rp_for_bounds and _rp_for_bounds.panel_layout and _rp_for_bounds.layers:
                    try:
                        _fl_key = next(iter(_rp_for_bounds.layers))
                        _fl_lyr = _rp_for_bounds.layers[_fl_key]
                        _origins, _cw, _ch = compute_cm_geometry(
                            tuple(tuple(p) for p in _rp_for_bounds.panel_layout.unit_positions),
                            tuple(_fl_lyr.bounds),
                        )
                        _x_vals = [_origins.get((0, _c), (_c * _cw, 0))[0] + _cw / 2
                                   for _c in range(_n_cols)]
                        _y_vals = [_origins.get((_r, 0), (0, _r * _ch))[1] + _ch / 2
                                   for _r in range(_n_rows)]
                        _x_label, _y_label = "Panel X (mm)", "Panel Y (mm)"
                        _use_mm = True
                    except Exception:
                        pass

                # ── Rich hover: shows both raw count + repeatability ───────
                _hover = _np_hm.empty((_n_rows, _n_cols), dtype=object)
                for _ri in range(_n_rows):
                    for _ci in range(_n_cols):
                        _raw  = int(_Z_raw[_ri, _ci])
                        _mask = ((hm_df['UNIT_INDEX_X'] == _ci) &
                                 (hm_df['UNIT_INDEX_Y'] == _ri)) if _has_idx else None
                        _n_p  = (hm_df.loc[_mask, _panel_col].nunique()
                                 if (_mask is not None and _panel_col in hm_df.columns) else 0)
                        _pct  = f"{_n_p / _n_panels_total * 100:.0f}%" if _n_panels_total else "—"
                        _hover[_ri, _ci] = (
                            f"<b>Col {_ci} · Row {_ri}</b><br>"
                            f"Defects: {_raw}<br>"
                            f"Panels with defect: {_n_p}/{_n_panels_total} ({_pct})"
                        )

                _grid_cs = [
                    [0.0,   'rgba(6,20,6,0.0)'],
                    [0.001, 'rgba(0,120,70,0.85)'],
                    [0.4,   'rgba(255,180,0,0.9)'],
                    [1.0,   'rgba(220,30,30,1.0)'],
                ]

                _grid_fig = go.Figure(go.Heatmap(
                    z=_Z,
                    x=_x_vals,
                    y=_y_vals,
                    colorscale=_grid_cs,
                    text=[[
                        f"{int(_v)}{_label_suffix}" if _v > 0 else ''
                        for _v in _row
                    ] for _row in _Z],
                    texttemplate='%{text}',
                    hovertext=_hover.tolist(),
                    hovertemplate='%{hovertext}<extra></extra>',
                    xgap=2, ygap=2,
                    colorbar=dict(
                        title=dict(text=_cb_title, side="right"),
                        thickness=12, len=0.65,
                    ),
                ))

                # Frame border — coords must match the axis units (mm or unit indices)
                if _use_mm:
                    _fx0, _fy0, _fx1, _fy1 = frame_bx1, frame_by1, frame_bx2, frame_by2
                    _fp = 8
                else:
                    _fx0, _fy0 = -0.5, -0.5
                    _fx1, _fy1 = _n_cols - 0.5, _n_rows - 0.5
                    _fp = 0

                _grid_fig.add_shape(
                    type="rect", x0=_fx0 - _fp, y0=_fy0 - _fp,
                    x1=_fx1 + _fp, y1=_fy1 + _fp,
                    fillcolor="#2B3A2B", line=dict(color="#1a2a1a", width=1), layer="below",
                )
                _grid_fig.add_shape(
                    type="rect", x0=_fx0, y0=_fy0,
                    x1=_fx1, y1=_fy1,
                    fillcolor="rgba(0,0,0,0)", line=dict(color="#C87533", width=2),
                )

                # ── True-scale: enforce equal mm per pixel on both axes ────
                _grid_fig.update_layout(
                    plot_bgcolor='#060A06',
                    paper_bgcolor='#0d0d0d',
                    font=dict(color='#e0e0e0'),
                    height=600,
                    margin=dict(l=60, r=60, t=30, b=60),
                    xaxis=dict(title=_x_label, showgrid=False, zeroline=False, color='#aaa'),
                    yaxis=dict(
                        title=_y_label, showgrid=False, zeroline=False, color='#aaa',
                        autorange=True,
                        **({'scaleanchor': 'x', 'scaleratio': 1} if _use_mm else {}),
                    ),
                )

                # ── Summary metrics ────────────────────────────────────────
                _col_sums = _Z_raw.sum(axis=0)   # sum per column
                _row_sums = _Z_raw.sum(axis=1)   # sum per row
                _worst_col_i = int(_np_hm.argmax(_col_sums))
                _worst_row_i = int(_np_hm.argmax(_row_sums))
                _total_def   = int(_Z_raw.sum())

                _mc1, _mc2, _mc3, _mc4 = st.columns(4)
                _mc1.metric("Total Defects", f"{_total_def:,}")
                _mc2.metric("Panels Included", f"{len(_sel_sources) if _sel_sources else _n_panels_total}")
                _mc3.metric("Worst Column", f"Col {_worst_col_i}",
                            delta=f"{int(_col_sums[_worst_col_i])} defects",
                            delta_color="inverse")
                _mc4.metric("Worst Row", f"Row {_worst_row_i}",
                            delta=f"{int(_row_sums[_worst_row_i])} defects",
                            delta_color="inverse")

                # ── Main chart (click-to-drill) ────────────────────────────
                _grid_event = st.plotly_chart(
                    _grid_fig, on_select="rerun", width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False},
                    key="unit_grid_chart",
                )

                # Drill-into-unit when a cell is clicked
                _sel_pts = (_grid_event or {}).get('selection', {}).get('points', [])
                if _sel_pts:
                    _pt = _sel_pts[0]
                    if _use_mm:
                        _drill_col = int(_np_hm.argmin([abs(_pt.get('x', 0) - _xv) for _xv in _x_vals]))
                        _drill_row = int(_np_hm.argmin([abs(_pt.get('y', 0) - _yv) for _yv in _y_vals]))
                    else:
                        _drill_col = int(round(_pt.get('x', 0)))
                        _drill_row = int(round(_pt.get('y', 0)))
                    _drill_count = int(_Z_raw[_drill_row, _drill_col])
                    _drill_mask  = ((hm_df['UNIT_INDEX_X'] == _drill_col) &
                                    (hm_df['UNIT_INDEX_Y'] == _drill_row)) if _has_idx else None
                    _drill_pct   = (hm_df.loc[_drill_mask, _panel_col].nunique()
                                    / _n_panels_total * 100) if (_drill_mask is not None
                                    and _panel_col in hm_df.columns) else 0
                    st.info(
                        f"Selected **Col {_drill_col} · Row {_drill_row}** — "
                        f"{_drill_count} defects · {_drill_pct:.0f}% repeatability"
                    )
                    if st.button(
                        f"🔬 Drill into Unit (Col {_drill_col} · Row {_drill_row})",
                        key="drill_unit_btn",
                    ):
                        st.session_state['_view_mode'] = "🗺️ Unit Commonality"
                        st.session_state['drill_unit_col'] = _drill_col
                        st.session_state['drill_unit_row'] = _drill_row
                        st.rerun()

                # ── Position effect bar charts ─────────────────────────────
                st.markdown("**Position Effect Analysis**")
                _bar_c1, _bar_c2 = st.columns(2)

                _bar_col_fig = go.Figure(go.Bar(
                    x=[f"C{_i}" for _i in range(_n_cols)],
                    y=_col_sums.tolist(),
                    marker_color=[
                        f"rgba(220,{max(0,180-int(_v/_col_sums.max()*180))},0,0.85)"
                        if _col_sums.max() > 0 else 'rgba(0,120,70,0.7)'
                        for _v in _col_sums
                    ],
                    hovertemplate='Col %{x}: <b>%{y:.0f}</b> defects<extra></extra>',
                ))
                _bar_col_fig.update_layout(
                    title=dict(text="Defects by Column  (left ↔ right machine effect)",
                               font=dict(size=12, color='#aaa')),
                    plot_bgcolor='#060A06', paper_bgcolor='#0d0d0d',
                    font=dict(color='#ccc'), height=220,
                    margin=dict(l=40, r=10, t=40, b=30),
                    xaxis=dict(showgrid=False, zeroline=False),
                    yaxis=dict(showgrid=False, zeroline=False),
                    bargap=0.15,
                )
                _bar_c1.plotly_chart(_bar_col_fig, width='stretch',
                                     config={'displayModeBar': False})

                _bar_row_fig = go.Figure(go.Bar(
                    x=[f"R{_i}" for _i in range(_n_rows)],
                    y=_row_sums.tolist(),
                    marker_color=[
                        f"rgba(220,{max(0,180-int(_v/_row_sums.max()*180))},0,0.85)"
                        if _row_sums.max() > 0 else 'rgba(0,120,70,0.7)'
                        for _v in _row_sums
                    ],
                    hovertemplate='Row %{x}: <b>%{y:.0f}</b> defects<extra></extra>',
                ))
                _bar_row_fig.update_layout(
                    title=dict(text="Defects by Row  (front ↔ back machine effect)",
                               font=dict(size=12, color='#aaa')),
                    plot_bgcolor='#060A06', paper_bgcolor='#0d0d0d',
                    font=dict(color='#ccc'), height=220,
                    margin=dict(l=40, r=10, t=40, b=30),
                    xaxis=dict(showgrid=False, zeroline=False),
                    yaxis=dict(showgrid=False, zeroline=False),
                    bargap=0.15,
                )
                _bar_c2.plotly_chart(_bar_row_fig, width='stretch',
                                     config={'displayModeBar': False})

                st.caption(
                    "**Repeatability %** = % of panels where that unit had ≥1 defect — "
                    "use this to separate systematic machine faults from one-off incidents. "
                    "**Column bar** = left/right squeegee or conveyor wear. "
                    "**Row bar** = front/back oven temperature gradient. "
                    "Click any cell in the grid to drill into Unit Commonality for that position."
                )

    else:
        st.info("Upload AOI defect data to view the Panel Heatmap.")
