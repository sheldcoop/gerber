
            if not _cm_sel_units:
                st.info("Select at least one unit to display.")
            else:
                # ── Panel geometry (fallback dimensions only) ─────────────────
                _ctx_cm = calculate_geometry(_q_rows_cm, _q_cols_cm, _d_gap_x_cm, _d_gap_y_cm)

                # ── Derive exact cell size and unit origins from TGZ ──────────
                # Prefer TGZ step-repeat positions + CAM layer bounds over manual
                # geometry. Manual geometry is used only when TGZ is not loaded.
                #
                # How it works:
                #   TGZ unit_positions[i] = (x_tgz, y_tgz): where the ODB++ unit
                #   step's local (0,0) is placed in panel coordinates.
                #   cam_bounds = (min_x, min_y, max_x, max_y) in ODB++ local coords.
                #   Effective origin = (x_tgz + min_x, y_tgz + min_y) = bottom-left
                #   of the copper area in panel coords.
                #   Normalised defect: x_local = X_MM - effective_origin_x
                #   This lands in [0, cell_w] and matches the CAM SVG which is also
                #   shifted to [0, cell_w] × [0, cell_h] in the plot.
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
                    _cm_origins, _cam_cell_w, _cam_cell_h = _compute_cm_geometry(
                        unit_positions=tuple(_rodb_cm.panel_layout.unit_positions),
                        first_layer_bounds=tuple(_first_lyr_cm.bounds),
                    )
                    _cam_min_x = _first_lyr_cm.bounds[0]
                    _cam_min_y = _first_lyr_cm.bounds[1]
                else:
                    # No TGZ — derive unit origins from sidebar geometry (same formula
                    # as alignment.py so sample data aligns correctly without CAM)
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

                # ── Scope-filter AOI data (cached) ────────────────────────────
                _bu_cm   = st.session_state.get('buildup_filter_select', aoi.buildup_numbers)
                _side_cm = st.session_state.get('scope_side_sel', ['Front', 'Back'])
                _cm_src  = _filter_aoi_cm(
                    aoi.all_defects,
                    tuple(sorted(_bu_cm)) if _bu_cm else (),
                    tuple(sorted(_side_cm)),
                )

                # Filter to selected units only
                _cm_src = _cm_src.copy()
                _cm_src['_ukey'] = list(zip(
                    _cm_src['UNIT_INDEX_Y'].astype(int),
                    _cm_src['UNIT_INDEX_X'].astype(int),
                ))
                _cm_src = _cm_src[_cm_src['_ukey'].isin(set(_cm_sel_units))].copy()
                _cm_src.drop(columns=['_ukey'], inplace=True)

                if _cm_src.empty:
                    st.info("No defects found for the selected units / scope filters.")
                    # Still render the CAM design so the user can see the reference unit
                    _rodb_cm_empty = st.session_state.get('rendered_odb')
                    if _rodb_cm_empty and _rodb_cm_empty.layers and _first_lyr_cm:
                        # Collect ALL checked layers — support multi-layer stack
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
                                    source=_get_svg_url(_em_l),
                                    xref="x", yref="y",
                                    x=_lyr_b_em[0] + _ref_sx_em,
                                    y=_lyr_b_em[3] + _ref_sy_em,
                                    sizex=_lyr_b_em[2] - _lyr_b_em[0],
                                    sizey=_lyr_b_em[3] - _lyr_b_em[1],
                                    sizing="stretch", layer="below",
                                    opacity=_layer_opacity(_em_n, _em_l.layer_type, _is_multi_em),
                                ))
                            # Layer name label (top-centre green)
                            _em_fig.add_annotation(
                                x=_cam_cell_w / 2, y=_cam_cell_h + _cam_cell_h * 0.04,
                                text=f"Layer: {_em_lbl}",
                                showarrow=False, xanchor="center", yanchor="bottom",
                                font=dict(color="rgba(0,220,130,0.95)", size=12, family="monospace"),
                                xref="x", yref="y",
                            )
                            # Dimension annotations
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
                            st.plotly_chart(_em_fig, width='stretch',

                                            config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})
                else:
                    st.info("No defects found for the selected units. Upload AOI data for analysis.")

                st.divider()

                # ── Position effect bar charts ─────────────────────────────
                st.markdown("**Position Effect Analysis**")

                # Prepare row/column defect counts for Position Effect charts
                _cm_row_inds = sorted(set(r for r, c in _all_cm_pairs))
                _cm_col_inds = sorted(set(c for r, c in _all_cm_pairs))
                _n_rows = len(_cm_row_inds)
                _n_cols = len(_cm_col_inds)

                if not _cm_src.empty:
                    _col_counts = _cm_src.groupby('UNIT_INDEX_X', observed=True).size()
                    _row_counts = _cm_src.groupby('UNIT_INDEX_Y', observed=True).size()
                else:
                    _col_counts = pd.Series(dtype=int)
                    _row_counts = pd.Series(dtype=int)

                _col_sums = pd.Series([_col_counts.get(c, 0) for c in _cm_col_inds])
                _row_sums = pd.Series([_row_counts.get(r, 0) for r in _cm_row_inds])

                _bar_c1, _bar_c2 = st.columns(2)

                _bar_col_fig = go.Figure(go.Bar(
                    x=[f"C{c}" for c in _cm_col_inds],
                    y=_col_sums.tolist(),
                    marker_color=[
                        f"rgba(220,{max(0,180-int(_v/_col_sums.max()*180))},0,0.85)"
                        if _col_sums.max() > 0 else 'rgba(0,120,70,0.7)'
                        for _v in _col_sums
                    ],
                    hovertemplate='Col %{x}: <b>%{y:.0f}</b> defects<extra></extra>',
                ))
