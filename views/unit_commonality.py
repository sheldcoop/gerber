import math
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from alignment import calculate_geometry, INTER_UNIT_GAP
from visualizer import build_defect_only_figure, OverlayConfig, _apply_layout
from core.data_utils import compute_cm_geometry, filter_aoi_cm

def render_unit_commonality(parsed, aoi, align_args, get_svg_url):
    st.markdown("### 🗺️ Commonality — Defect Superposition")
    st.caption("Normalise each selected unit's defects into local coordinates and overlay on a single reference unit.")

    _LAYER_Z = {'drill': 0, 'other': 1, 'paste': 2,
                'soldermask': 3, 'silkscreen': 4, 'outline': 5, 'copper': 6}
    _LAYER_OPACITY_SINGLE = {'copper': 0.95, 'drill': 0.55, 'other': 0.60}
    _LAYER_OPACITY_MULTI  = {'copper': 0.90, 'drill': 0.45, 'other': 0.50}

    def _layer_sort_key(name_lyr_pair):
        return _LAYER_Z.get(name_lyr_pair[1].layer_type, 1)

    def _layer_opacity(layer_name, lyr_type, multi):
        slider_val = st.session_state.get(f"opacity_{layer_name}")
        if slider_val is not None:
            return float(slider_val)
        d = _LAYER_OPACITY_MULTI if multi else _LAYER_OPACITY_SINGLE
        return d.get(lyr_type, 0.70 if multi else 0.85)

    _rodb_cm_check = st.session_state.get('rendered_odb')
    _has_aoi_cm = (
        aoi and aoi.has_data
        and 'UNIT_INDEX_X' in aoi.all_defects.columns
        and 'UNIT_INDEX_Y' in aoi.all_defects.columns
    )

    if not _rodb_cm_check and not _has_aoi_cm:
        st.info("Upload a TGZ design file or AOI defect data to use this view.")

    elif not _has_aoi_cm:
        st.info("ℹ️ Upload AOI defect data to overlay defects on the design.")
        if _rodb_cm_check and _rodb_cm_check.layers:
            _no_aoi_ref_lyr = next(
                (l for l in _rodb_cm_check.layers.values() if l.layer_type != 'drill'),
                next(iter(_rodb_cm_check.layers.values()))
            )
            if _rodb_cm_check.panel_layout:
                _, _no_aoi_cw, _no_aoi_ch = compute_cm_geometry(
                    unit_positions=tuple(_rodb_cm_check.panel_layout.unit_positions),
                    first_layer_bounds=tuple(_no_aoi_ref_lyr.bounds),
                )
            else:
                _rb_na = _no_aoi_ref_lyr.bounds
                _no_aoi_cw = _rb_na[2] - _rb_na[0]
                _no_aoi_ch = _rb_na[3] - _rb_na[1]

            _na_checked = [
                (_na_n, _na_l)
                for _na_n, _na_l in _rodb_cm_check.layers.items()
                if st.session_state.get(f"vis_{_na_n}", False)
            ]

            if not _na_checked:
                st.caption("☝️ Select a layer in the sidebar to view the design.")
            else:
                _ref_b_na  = _no_aoi_ref_lyr.bounds
                _ref_sx_na = -_ref_b_na[0]
                _ref_sy_na = -_ref_b_na[1]
                _is_multi_na = len(_na_checked) > 1
                _na_sorted = sorted(_na_checked, key=_layer_sort_key)

                _design_fig = go.Figure()
                for _na_n, _na_l in _na_sorted:
                    _lyr_b_na = _na_l.bounds
                    _design_fig.add_layout_image(dict(
                        source=get_svg_url(_na_l),
                        xref="x", yref="y",
                        x=_lyr_b_na[0] + _ref_sx_na,
                        y=_lyr_b_na[3] + _ref_sy_na,
                        sizex=_lyr_b_na[2] - _lyr_b_na[0],
                        sizey=_lyr_b_na[3] - _lyr_b_na[1],
                        sizing="stretch", layer="below",
                        opacity=_layer_opacity(_na_n, _na_l.layer_type, _is_multi_na),
                    ))

                _lbl_na = " + ".join(n for n, _ in _na_checked)
                _design_fig.add_annotation(
                    x=_no_aoi_cw / 2, y=-_no_aoi_ch * 0.045,
                    text=f"W: {_no_aoi_cw:.2f} mm", showarrow=False,
                    font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
                    xref="x", yref="y",
                )
                _design_fig.add_annotation(
                    x=-_no_aoi_cw * 0.045, y=_no_aoi_ch / 2,
                    text=f"H: {_no_aoi_ch:.2f} mm", showarrow=False, textangle=-90,
                    font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
                    xref="x", yref="y",
                )
                _design_fig.add_annotation(
                    x=_no_aoi_cw / 2, y=_no_aoi_ch + _no_aoi_ch * 0.04,
                    text=f"Layer: {_lbl_na}",
                    showarrow=False, xanchor="center", yanchor="bottom",
                    font=dict(color="rgba(0,220,130,0.95)", size=12, family="monospace"),
                    xref="x", yref="y",
                )
                _design_fig.update_layout(
                    xaxis=dict(range=[-1, _no_aoi_cw + 1], scaleanchor='y', scaleratio=1,
                               showgrid=False, zeroline=False, showticklabels=False),
                    yaxis=dict(range=[-1, _no_aoi_ch + 1], showgrid=False,
                               zeroline=False, showticklabels=False),
                    plot_bgcolor='#000000', paper_bgcolor='#000000',
                    font=dict(color='#cccccc'),
                    margin=dict(l=0, r=0, t=36, b=0), height=600,
                )
                st.plotly_chart(_design_fig, width='stretch',
                                config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})

    else:
        _q_rows_cm  = int(st.session_state.get('quad_rows_input', 6))
        _q_cols_cm  = int(st.session_state.get('quad_cols_input', 6))
        _d_gap_x_cm = float(st.session_state.get('dyn_gap_x_input', 5.0))
        _d_gap_y_cm = float(st.session_state.get('dyn_gap_y_input', 3.5))

        _rodb_cm_pl = st.session_state.get('rendered_odb')
        if _rodb_cm_pl and _rodb_cm_pl.panel_layout:
            _pl_cm  = _rodb_cm_pl.panel_layout
            _rp_cm  = _pl_cm.unit_positions
            _uxs_cm = sorted(set(round(x, 2) for x, _ in _rp_cm))
            _uys_cm = sorted(set(round(y, 2) for _, y in _rp_cm))
            _all_cm_pairs = [(ri, ci)
                             for ri in range(len(_uys_cm))
                             for ci in range(len(_uxs_cm))]
            _q_rows_cm = max(1, len(_uys_cm) // 2)
            _q_cols_cm = max(1, len(_uxs_cm) // 2)
        else:
            _aup = (
                aoi.all_defects[['UNIT_INDEX_Y', 'UNIT_INDEX_X']]
                .dropna()
                .drop_duplicates()
                .sort_values(['UNIT_INDEX_Y', 'UNIT_INDEX_X'])
                .values.tolist()
            )
            _all_cm_pairs = [(int(r), int(c)) for r, c in _aup]
        _all_cm_labels = [f"({r},{c})" for r, c in _all_cm_pairs]

        def _cm_quad(r, c):
            qr, qc = r // _q_rows_cm, c // _q_cols_cm
            return {(0,0):'Q2',(0,1):'Q3',(1,0):'Q1',(1,1):'Q4'}.get((qr, qc), 'Other')

        _q1_cm_lbl = [l for (r,c),l in zip(_all_cm_pairs,_all_cm_labels) if _cm_quad(r,c)=='Q1']
        _q2_cm_lbl = [l for (r,c),l in zip(_all_cm_pairs,_all_cm_labels) if _cm_quad(r,c)=='Q2']
        _q3_cm_lbl = [l for (r,c),l in zip(_all_cm_pairs,_all_cm_labels) if _cm_quad(r,c)=='Q3']
        _q4_cm_lbl = [l for (r,c),l in zip(_all_cm_pairs,_all_cm_labels) if _cm_quad(r,c)=='Q4']

        if 'cm_multiselect' not in st.session_state:
            st.session_state['cm_multiselect'] = _all_cm_labels

        def _cm_set(labels):
            def cb():
                st.session_state['cm_multiselect'] = [l for l in labels if l in _all_cm_labels]
            return cb

        _qs_cm = st.columns(6, gap="small")
        _qs_cm[0].button("ALL",   key="cm_all",   on_click=_cm_set(_all_cm_labels), width="stretch", type="primary")
        _qs_cm[1].button("Q1",    key="cm_q1",    on_click=_cm_set(_q1_cm_lbl),     width="stretch")
        _qs_cm[2].button("Q2",    key="cm_q2",    on_click=_cm_set(_q2_cm_lbl),     width="stretch")
        _qs_cm[3].button("Q3",    key="cm_q3",    on_click=_cm_set(_q3_cm_lbl),     width="stretch")
        _qs_cm[4].button("Q4",    key="cm_q4",    on_click=_cm_set(_q4_cm_lbl),     width="stretch")
        _qs_cm[5].button("Clear", key="cm_clear", on_click=_cm_set([]),             width="stretch")

        _cur_cm_lbl = [l for l in st.session_state.get('cm_multiselect', []) if l in _all_cm_labels]
        if _cur_cm_lbl != st.session_state.get('cm_multiselect'):
            st.session_state['cm_multiselect'] = _cur_cm_lbl

        _sel_cm_labels = st.multiselect(
            "Selected units (row, col)",
            options=_all_cm_labels,
            key='cm_multiselect',
            help="Choose which units' defects to superimpose. Use the quick-select buttons above for bulk selection.",
        )

        _cm_sel_units = []
        for _lbl in _sel_cm_labels:
            try:
                _r2, _c2 = _lbl.strip('()').split(',')
                _cm_sel_units.append((int(_r2.strip()), int(_c2.strip())))
            except Exception:
                pass

        st.caption(f"**{len(_cm_sel_units)}** / {len(_all_cm_pairs)} units selected")
        st.divider()

        if not _cm_sel_units:
            st.info("Select at least one unit to display.")
        else:
            _ctx_cm = calculate_geometry(_q_rows_cm, _q_cols_cm, _d_gap_x_cm, _d_gap_y_cm)

            _rodb_cm    = st.session_state.get('rendered_odb')
            _cam_cell_w = _ctx_cm.cell_width
            _cam_cell_h = _ctx_cm.cell_height
            _cam_min_x  = 0.0
            _cam_min_y  = 0.0

            if _rodb_cm and _rodb_cm.panel_layout and _rodb_cm.layers:
                _first_lyr_cm = next(
                    (l for l in _rodb_cm.layers.values() if l.layer_type != 'drill'),
                    next(iter(_rodb_cm.layers.values()))
                )
                _cm_origins, _cam_cell_w, _cam_cell_h = compute_cm_geometry(
                    unit_positions=tuple(_rodb_cm.panel_layout.unit_positions),
                    first_layer_bounds=tuple(_first_lyr_cm.bounds),
                )
                _cam_min_x = _first_lyr_cm.bounds[0]
                _cam_min_y = _first_lyr_cm.bounds[1]
            else:
                _no_tgz_ctx = calculate_geometry(_q_rows_cm, _q_cols_cm, _d_gap_x_cm, _d_gap_y_cm)
                _cam_cell_w = _no_tgz_ctx.cell_width
                _cam_cell_h = _no_tgz_ctx.cell_height
                _all_orig_pos_nt: list[tuple[float, float]] = []
                for _qox_nt, _qoy_nt in _no_tgz_ctx.quadrant_origins.values():
                    for _rr_nt in range(_q_rows_cm):
                        for _cc_nt in range(_q_cols_cm):
                            _all_orig_pos_nt.append((
                                _qox_nt + INTER_UNIT_GAP + _cc_nt * _no_tgz_ctx.stride_x,
                                _qoy_nt + INTER_UNIT_GAP + _rr_nt * _no_tgz_ctx.stride_y,
                            ))
                _uo_xs_nt = sorted(set(round(x, 2) for x, _ in _all_orig_pos_nt))
                _uo_ys_nt = sorted(set(round(y, 2) for _, y in _all_orig_pos_nt))
                _cm_origins = {
                    (ri, ci): (_uo_xs_nt[ci], _uo_ys_nt[ri])
                    for ri in range(len(_uo_ys_nt))
                    for ci in range(len(_uo_xs_nt))
                }

            _bu_cm   = st.session_state.get('buildup_filter_select', aoi.buildup_numbers)
            _side_cm = st.session_state.get('scope_side_sel', ['Front', 'Back'])
            _cm_src  = filter_aoi_cm(
                aoi.all_defects,
                tuple(sorted(_bu_cm)) if _bu_cm else (),
                tuple(sorted(_side_cm)),
            )

            _cm_src = _cm_src.copy()
            _cm_src['_ukey'] = list(zip(
                _cm_src['UNIT_INDEX_Y'].astype(int),
                _cm_src['UNIT_INDEX_X'].astype(int),
            ))
            _cm_src = _cm_src[_cm_src['_ukey'].isin(set(_cm_sel_units))].copy()
            _cm_src.drop(columns=['_ukey'], inplace=True)

            if _cm_src.empty:
                st.info("No defects found for the selected units / scope filters.")
                _rodb_cm_empty = st.session_state.get('rendered_odb')
                if _rodb_cm_empty and _rodb_cm_empty.layers and _first_lyr_cm:
                    _em_checked = [
                        (_em_n, _em_l)
                        for _em_n, _em_l in _rodb_cm_empty.layers.items()
                        if st.session_state.get(f"vis_{_em_n}", False)
                    ]
                    if not _em_checked:
                        st.caption("☝️ Select a layer in the sidebar to view the design.")
                    else:
                        _ref_b_em   = _first_lyr_cm.bounds
                        _ref_sx_em  = -_ref_b_em[0]
                        _ref_sy_em  = -_ref_b_em[1]
                        _is_multi_em = len(_em_checked) > 1
                        _em_sorted  = sorted(_em_checked, key=_layer_sort_key)
                        _em_fig = go.Figure()
                        for _em_n, _em_l in _em_sorted:
                            _lyr_b_em = _em_l.bounds
                            _em_fig.add_layout_image(dict(
                                source=get_svg_url(_em_l),
                                xref="x", yref="y",
                                x=_lyr_b_em[0] + _ref_sx_em,
                                y=_lyr_b_em[3] + _ref_sy_em,
                                sizex=_lyr_b_em[2] - _lyr_b_em[0],
                                sizey=_lyr_b_em[3] - _lyr_b_em[1],
                                sizing="stretch", layer="below",
                                opacity=_layer_opacity(_em_n, _em_l.layer_type, _is_multi_em),
                            ))
                        _em_lbl = " + ".join(n for n, _ in _em_checked)
                        _em_fig.add_annotation(
                            x=_cam_cell_w / 2, y=_cam_cell_h + _cam_cell_h * 0.04,
                            text=f"Layer: {_em_lbl}",
                            showarrow=False, xanchor="center", yanchor="bottom",
                            font=dict(color="rgba(0,220,130,0.95)", size=12, family="monospace"),
                            xref="x", yref="y",
                        )
                        _em_fig.add_annotation(
                            x=_cam_cell_w / 2, y=-_cam_cell_h * 0.045,
                            text=f"W: {_cam_cell_w:.2f} mm", showarrow=False,
                            font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
                            xref="x", yref="y",
                        )
                        _em_fig.add_annotation(
                            x=-_cam_cell_w * 0.045, y=_cam_cell_h / 2,
                            text=f"H: {_cam_cell_h:.2f} mm", showarrow=False, textangle=-90,
                            font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
                            xref="x", yref="y",
                        )
                        _em_fig.update_layout(
                            xaxis=dict(range=[-1, _cam_cell_w + 1], scaleanchor='y', scaleratio=1,
                                       showgrid=False, zeroline=False, showticklabels=False),
                            yaxis=dict(range=[-1, _cam_cell_h + 1], showgrid=False,
                                       zeroline=False, showticklabels=False),
                            plot_bgcolor='#000000', paper_bgcolor='#000000',
                            font=dict(color='#cccccc'),
                            margin=dict(l=0, r=0, t=36, b=0), height=600,
                        )
                        st.plotly_chart(_em_fig, width='stretch',
                                        config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})
            else:
                _pairs_cm = list(zip(
                    _cm_src['UNIT_INDEX_Y'].astype(int),
                    _cm_src['UNIT_INDEX_X'].astype(int),
                ))
                _ox_arr = [_cm_origins.get(p, (0.0, 0.0))[0] for p in _pairs_cm]
                _oy_arr = [_cm_origins.get(p, (0.0, 0.0))[1] for p in _pairs_cm]

                _cm_off_x = align_args.get('manual_offset_x', 0.0)
                _cm_off_y = align_args.get('manual_offset_y', 0.0)
                _cm_plot = _cm_src.copy()
                # unit_positions already encode the copper-left-edge in display space
                # (build_panel_svg places the copper edge at x=x_mm via tx=x_mm-vx).
                # So ALIGNED = X_MM - unit_pos maps the defect to the correct Plotly
                # position without any extra cam_min correction.
                _cm_plot['ALIGNED_X'] = _cm_src['X_MM'].values - _ox_arr + _cm_off_x
                _cm_plot['ALIGNED_Y'] = _cm_src['Y_MM'].values - _oy_arr + _cm_off_y

                # ── Rotation control ──────────────────────────────────────
                _rot_deg = st.number_input(
                    "Rotation (°)", min_value=0.0, max_value=360.0,
                    value=0.0, step=0.5, format="%.1f",
                    key='cm_rotation_deg',
                    help="Rotate all defect points around the unit centre.",
                )
                if abs(_rot_deg) > 0.01:
                    _cx = _cam_cell_w / 2
                    _cy = _cam_cell_h / 2
                    _rad = math.radians(_rot_deg)
                    _cos_r, _sin_r = math.cos(_rad), math.sin(_rad)
                    _dx = _cm_plot['ALIGNED_X'] - _cx
                    _dy = _cm_plot['ALIGNED_Y'] - _cy
                    _cm_plot = _cm_plot.copy()
                    _cm_plot['ALIGNED_X'] = _cx + _dx * _cos_r - _dy * _sin_r
                    _cm_plot['ALIGNED_Y'] = _cy + _dx * _sin_r + _dy * _cos_r

                _cm_cfg = OverlayConfig()
                _cm_cfg.board_bounds = (
                    -1.0, -1.0,
                    _cam_cell_w + 1.0,
                    _cam_cell_h + 1.0,
                )
                _cm_cfg.color_mode    = st.session_state.get('color_mode_select', 'by_type')
                _cm_cfg.marker_style  = st.session_state.get('marker_style_select', 'dot')
                _cm_cfg.buildup_filter = _bu_cm
                _cm_cfg.defect_types  = st.session_state.get('defect_type_select', aoi.defect_types)
                _cm_cfg.side_filter   = 'Both'

                _cm_fig = build_defect_only_figure(_cm_plot, _cm_cfg)

                _rendered_cm      = st.session_state.get('rendered_odb')
                _active_layer_name = None

                if _rendered_cm and _rendered_cm.layers:
                    _cm_cam_layers = [
                        n for n in _rendered_cm.layers
                        if st.session_state.get(f"vis_{n}", False)
                    ]

                    _is_multi_cm = len(_cm_cam_layers) > 1
                    _cm_cam_pairs = [
                        (ln, _rendered_cm.layers[ln])
                        for ln in _cm_cam_layers
                        if _rendered_cm.layers.get(ln)
                    ]
                    _cm_cam_pairs.sort(key=_layer_sort_key)
                    _active_layer_name = _cm_cam_pairs[-1][0] if _cm_cam_pairs else None
                    _ref_b_cm    = _first_lyr_cm.bounds
                    _ref_shift_x = -_ref_b_cm[0]
                    _ref_shift_y = -_ref_b_cm[1]
                    for _cm_cam_ln, _cm_cam_lyr in _cm_cam_pairs:
                        if _is_multi_cm and _cm_cam_lyr.color_svg_urls:
                            _cm_data_url = next(iter(_cm_cam_lyr.color_svg_urls.values()))
                        else:
                            _cm_data_url = get_svg_url(_cm_cam_lyr)

                        _cb_cm = _cm_cam_lyr.bounds
                        _im_x  = _cb_cm[0] + _ref_shift_x
                        _im_y  = _cb_cm[3] + _ref_shift_y
                        _im_w  = _cb_cm[2] - _cb_cm[0]
                        _im_h  = _cb_cm[3] - _cb_cm[1]
                        _cm_fig.add_layout_image(dict(
                            source=_cm_data_url,
                            xref="x", yref="y",
                            x=_im_x, y=_im_y,
                            sizex=_im_w, sizey=_im_h,
                            sizing="stretch", layer="below",
                            opacity=_layer_opacity(_cm_cam_ln, _cm_cam_lyr.layer_type, _is_multi_cm),
                        ))
                    _apply_layout(_cm_fig, _cm_cfg)
                else:
                    _cm_fig.add_shape(
                        type="rect", x0=0, y0=0, x1=_cam_cell_w, y1=_cam_cell_h,
                        line=dict(color="rgba(0,180,80,0.5)", width=1.5),
                        fillcolor="rgba(0,0,0,0)",
                        layer="below",
                    )
                    _apply_layout(_cm_fig, _cm_cfg)

                _grid_step = 5.0
                _gx = _grid_step
                while _gx < _cam_cell_w:
                    _cm_fig.add_shape(type="line",
                        x0=_gx, y0=0, x1=_gx, y1=_cam_cell_h,
                        line=dict(color="rgba(255,255,255,0.06)", width=1),
                        layer="below")
                    _gx += _grid_step
                _gy = _grid_step
                while _gy < _cam_cell_h:
                    _cm_fig.add_shape(type="line",
                        x0=0, y0=_gy, x1=_cam_cell_w, y1=_gy,
                        line=dict(color="rgba(255,255,255,0.06)", width=1),
                        layer="below")
                    _gy += _grid_step


                _cm_fig.add_annotation(
                    x=_cam_cell_w / 2, y=-_cam_cell_h * 0.045,
                    text=f"W: {_cam_cell_w:.2f} mm",
                    showarrow=False,
                    font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
                    xref="x", yref="y",
                )
                _cm_fig.add_annotation(
                    x=-_cam_cell_w * 0.045, y=_cam_cell_h / 2,
                    text=f"H: {_cam_cell_h:.2f} mm",
                    showarrow=False, textangle=-90,
                    font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
                    xref="x", yref="y",
                )

                if _active_layer_name:
                    _cm_fig.add_annotation(
                        x=_cam_cell_w / 2, y=_cam_cell_h + _cam_cell_h * 0.04,
                        text=f"Layer: {_active_layer_name}",
                        showarrow=False, xanchor="center", yanchor="bottom",
                        font=dict(color="rgba(0,220,130,0.95)", size=12, family="monospace"),
                        xref="x", yref="y",
                    )

                if len(_cm_plot) >= 5:
                    try:
                        from sklearn.neighbors import KernelDensity
                        import numpy as _np2
                        _hs_xy = _cm_plot[['ALIGNED_X', 'ALIGNED_Y']].dropna().values
                        if len(_hs_xy) >= 5:
                            _kde = KernelDensity(bandwidth=1.5, kernel='gaussian')
                            _kde.fit(_hs_xy)
                            _nx, _ny = 40, 40
                            _gxv = _np2.linspace(0, _cam_cell_w, _nx)
                            _gyv = _np2.linspace(0, _cam_cell_h, _ny)
                            _gxx, _gyy = _np2.meshgrid(_gxv, _gyv)
                            _grid_pts = _np2.column_stack([_gxx.ravel(), _gyy.ravel()])
                            _dens = _np2.exp(_kde.score_samples(_grid_pts)).reshape(_ny, _nx)
                            _peak_idx = _np2.unravel_index(_dens.argmax(), _dens.shape)
                            _hs_cx = float(_gxv[_peak_idx[1]])
                            _hs_cy = float(_gyv[_peak_idx[0]])
                            _hs_r  = min(_cam_cell_w, _cam_cell_h) * 0.06
                            _cm_fig.add_shape(type="circle",
                                x0=_hs_cx - _hs_r, y0=_hs_cy - _hs_r,
                                x1=_hs_cx + _hs_r, y1=_hs_cy + _hs_r,
                                line=dict(color="rgba(255,80,80,0.9)", width=2, dash="dot"),
                                fillcolor="rgba(255,80,80,0.08)", layer="above")
                            _cm_fig.add_annotation(
                                x=_hs_cx, y=_hs_cy + _hs_r + _cam_cell_h * 0.02,
                                text="hotspot", showarrow=False,
                                font=dict(color="rgba(255,100,100,0.9)", size=10, family="monospace"),
                                xref="x", yref="y",
                            )
                    except Exception:
                        pass

                _show_heatmap = st.toggle("🌡️ Density Heatmap", value=False,
                                          help="Overlay a 2D defect density heatmap instead of individual dots",
                                          key="cm_heatmap_toggle")
                if _show_heatmap and len(_cm_plot) >= 3:
                    try:
                        import numpy as _np3
                        _hm_x = _cm_plot['ALIGNED_X'].dropna().values
                        _hm_y = _cm_plot['ALIGNED_Y'].dropna().values
                        _hm_nx, _hm_ny = 60, 60
                        _hm_gx = _np3.linspace(0, _cam_cell_w, _hm_nx)
                        _hm_gy = _np3.linspace(0, _cam_cell_h, _hm_ny)
                        _hm_z, _, _ = _np3.histogram2d(_hm_y, _hm_x,
                            bins=[_hm_ny, _hm_nx],
                            range=[[0, _cam_cell_h], [0, _cam_cell_w]])
                        from scipy.ndimage import gaussian_filter as _gf
                        _hm_z = _gf(_hm_z.astype(float), sigma=2.0)
                        _cm_fig.add_trace(go.Heatmap(
                            z=_hm_z,
                            x=_hm_gx, y=_hm_gy,
                            colorscale='Hot',
                            opacity=0.55,
                            showscale=False,
                            hoverinfo='skip',
                        ))
                    except Exception:
                        st.warning("Heatmap requires scipy. Install with: pip install scipy")

                _n_def = len(_cm_plot)
                _n_units = len(_cm_sel_units)
                _cm_fig.update_layout(
                    title=dict(
                        text=f"{_n_def} defects · {_n_units} units · avg {_n_def/_n_units:.1f}/unit",
                        font=dict(color="rgba(180,180,180,0.8)", size=12, family="monospace"),
                        x=0.5, xanchor="center",
                    )
                )

                _export_col, _spacer = st.columns([1, 4])
                with _export_col:
                    try:
                        from export import export_current_view
                        _cm_png = export_current_view(_cm_fig, fmt='png', scale=3)
                        st.download_button(
                            "📷 Export PNG",
                            data=_cm_png,
                            file_name="commonality_unit.png",
                            mime="image/png",
                            width="stretch",
                        )
                    except Exception:
                        st.button("📷 Export PNG (kaleido required)", disabled=True, width="stretch")

                st.plotly_chart(
                    _cm_fig,
                    width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False},
                )

                _cm_s1, _cm_s2, _cm_s3, _cm_s4 = st.columns(4)
                _cm_s1.metric("Units Selected", len(_cm_sel_units))
                _cm_s2.metric("Defects Shown",  len(_cm_plot))
                _cm_s3.metric("Avg / Unit",     f"{len(_cm_plot)/len(_cm_sel_units):.1f}")
                _top_cm = (
                    str(_cm_plot['DEFECT_TYPE'].value_counts().index[0])
                    if 'DEFECT_TYPE' in _cm_plot.columns and not _cm_plot.empty
                    else '—'
                )
                _cm_s4.metric("Top Defect Type", _top_cm)
