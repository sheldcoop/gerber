import streamlit as st
import pandas as pd
from core.data_utils import compute_cm_geometry

def render_cluster_triage(parsed, aoi, align_args):
    st.markdown("### 🔬 Cluster Triage")
    st.caption("Automatically finds groups of defects that keep happening at the same location — ranked by severity.")

    _rodb_ct = st.session_state.get('rendered_odb')
    _has_aoi_ct = aoi and aoi.has_data

    if not _has_aoi_ct:
        st.info("Upload AOI defect data to start triage.")
    else:
        import plotly.express as _px2

        # ── Compute ALIGNED_X/Y directly (same logic as Commonality) ─────
        _ct_df = aoi.all_defects.copy()
        _ct_aligned = False

        if (_rodb_ct and _rodb_ct.panel_layout and _rodb_ct.layers
                and 'UNIT_INDEX_X' in _ct_df.columns and 'UNIT_INDEX_Y' in _ct_df.columns):
            _ct_ref_lyr = next(
                (l for l in _rodb_ct.layers.values() if l.layer_type != 'drill'),
                next(iter(_rodb_ct.layers.values()))
            )
            _ct_origins, _ct_cell_w, _ct_cell_h = compute_cm_geometry(
                unit_positions=tuple(_rodb_ct.panel_layout.unit_positions),
                first_layer_bounds=tuple(_ct_ref_lyr.bounds),
            )
            _ct_min_iy = int(_ct_df['UNIT_INDEX_Y'].min())
            _ct_min_ix = int(_ct_df['UNIT_INDEX_X'].min())
            _ct_pairs = list(zip(
                _ct_df['UNIT_INDEX_Y'].astype(int) - _ct_min_iy,
                _ct_df['UNIT_INDEX_X'].astype(int) - _ct_min_ix,
            ))
            _ct_ox = [_ct_origins.get(p, (0.0, 0.0))[0] for p in _ct_pairs]
            _ct_oy = [_ct_origins.get(p, (0.0, 0.0))[1] for p in _ct_pairs]
            _ct_df['ALIGNED_X'] = _ct_df['X_MM'].values - _ct_ox
            _ct_df['ALIGNED_Y'] = _ct_df['Y_MM'].values - _ct_oy
            _ct_aligned = True
        else:
            # No TGZ — fall back to raw coordinates (still useful for finding repeats)
            _ct_df['ALIGNED_X'] = _ct_df['X_MM']
            _ct_df['ALIGNED_Y'] = _ct_df['Y_MM']
            _ct_cell_w, _ct_cell_h = None, None
            st.caption("ℹ️ TGZ not loaded — clustering on raw AOI coordinates. Load TGZ for unit-aligned results.")

        _ct_xy = _ct_df[['ALIGNED_X', 'ALIGNED_Y']].dropna()

        if len(_ct_xy) < 5:
            st.info("Not enough defects for cluster analysis (need at least 5).")
        else:
            try:
                from sklearn.cluster import DBSCAN as _DBSCAN

                # ── Sensitivity controls ──────────────────────────────────
                _ct_c1, _ct_c2 = st.columns(2)
                _ct_eps = _ct_c1.slider(
                    "Cluster radius (mm) — how close defects must be to group together",
                    0.5, 5.0, 1.5, step=0.25, key="ct_eps"
                )
                _ct_min_s = _ct_c2.slider(
                    "Min defects per cluster — smaller = catch smaller groups",
                    2, 10, 3, step=1, key="ct_min_samples"
                )

                _labels = _DBSCAN(eps=_ct_eps, min_samples=_ct_min_s).fit_predict(_ct_xy.values)
                _ct_df = _ct_df.loc[_ct_xy.index].copy()
                _ct_df['_cluster'] = _labels

                # ── Build summary ─────────────────────────────────────────
                _rows = []
                for _cid in sorted(set(_labels)):
                    if _cid == -1:
                        continue
                    _cl = _ct_df[_ct_df['_cluster'] == _cid]
                    _cnt = len(_cl)
                    _bu_spread = _cl['BUILDUP'].nunique() if 'BUILDUP' in _cl.columns else 1
                    _cx = round(float(_cl['ALIGNED_X'].mean()), 2)
                    _cy = round(float(_cl['ALIGNED_Y'].mean()), 2)

                    _top_type_str = '—'
                    if 'DEFECT_TYPE' in _cl.columns and not _cl.empty:
                        _type_counts = _cl['DEFECT_TYPE'].value_counts()
                        if not _type_counts.empty:
                            _top_type = _type_counts.idxmax()
                            _top_pct = (_type_counts.iloc[0] / _cnt) * 100
                            _top_type_str = f"{_top_type} ({_top_pct:.0f}%)"

                    _top_verif_str = '—'
                    if 'VERIFICATION' in _cl.columns and not _cl.empty:
                        _verif_counts = _cl['VERIFICATION'].value_counts()
                        if not _verif_counts.empty:
                            _top_verif = _verif_counts.idxmax()
                            _top_verif_pct = (_verif_counts.iloc[0] / _cnt) * 100
                            _top_verif_str = f"{_top_verif} ({_top_verif_pct:.0f}%)"

                    _n_units   = _cl[['UNIT_INDEX_Y', 'UNIT_INDEX_X']].drop_duplicates().__len__() if 'UNIT_INDEX_Y' in _cl.columns else '—'
                    # Severity: count × buildup spread penalty
                    _severity  = round(_cnt * (1 + 0.5 * (_bu_spread - 1)), 1)
                    _rows.append({
                        'Cluster': _cid,
                        'Defects': _cnt,
                        'Units Affected': _n_units,
                        'Buildup Layers': _bu_spread,
                        'Severity ▼': _severity,
                        'Top Type': _top_type_str,
                        'Top Verification': _top_verif_str,
                        'X (mm)': _cx,
                        'Y (mm)': _cy,
                    })

                _noise_ct = int((_labels == -1).sum())
                _n_cl_ct  = len(_rows)

                st.divider()

                if not _rows:
                    st.info("No clusters found. Try increasing the cluster radius or lowering the min defects slider.")
                else:
                    _ct_summary = pd.DataFrame(_rows).sort_values('Severity ▼', ascending=False)

                    # ── Metrics row ───────────────────────────────────────
                    _m1, _m2, _m3, _m4 = st.columns(4)
                    _m1.metric("Clusters Found", _n_cl_ct)
                    _m2.metric("Clustered Defects", int((_labels != -1).sum()))
                    _m3.metric("Isolated Defects", _noise_ct)
                    _m4.metric("Cluster Rate", f"{int((_labels != -1).sum()) / len(_labels) * 100:.0f}%")

                    # ── Critical callout ──────────────────────────────────
                    _top_ct = _ct_summary.iloc[0]
                    if _top_ct['Buildup Layers'] > 1:
                        st.error(
                            f"⚠️ **Critical — multi-layer cluster**: Cluster {int(_top_ct['Cluster'])} "
                            f"spans **{int(_top_ct['Buildup Layers'])} buildup layers** at "
                            f"({_top_ct['X (mm)']}, {_top_ct['Y (mm)']}) mm. "
                            f"Same location failing on multiple layers = process or registration issue. "
                            f"Severity: **{_top_ct['Severity ▼']}**"
                        )
                    else:
                        st.warning(
                            f"Highest severity: **{int(_top_ct['Defects'])} defects** at "
                            f"({_top_ct['X (mm)']}, {_top_ct['Y (mm)']}) mm — "
                            f"{_top_ct['Top Type']}. Severity: **{_top_ct['Severity ▼']}**"
                        )

                    # ── Ranked table ──────────────────────────────────────
                    st.dataframe(
                        _ct_summary,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            'Severity ▼': st.column_config.ProgressColumn(
                                'Severity ▼', min_value=0,
                                max_value=float(_ct_summary['Severity ▼'].max()),
                                format='%.1f',
                            ),
                            'Defects': st.column_config.NumberColumn('Defects', format='%d'),
                            'Units Affected': st.column_config.NumberColumn('Units Affected', format='%d'),
                        },
                    )

                    st.divider()
                    _tp1, _tp2 = st.columns(2)

                    # Bar: severity per cluster
                    with _tp1:
                        _sev_fig = _px2.bar(
                            _ct_summary, x='Cluster', y='Severity ▼',
                            color='Severity ▼', color_continuous_scale='OrRd',
                            title='Severity by Cluster',
                            text='Severity ▼',
                        )
                        _sev_fig.update_traces(texttemplate='%{text:.1f}', textposition='outside')
                        _sev_fig.update_layout(
                            plot_bgcolor='#000000', paper_bgcolor='#000000',
                            font=dict(color='#cccccc'), showlegend=False,
                            coloraxis_showscale=False,
                            margin=dict(l=0, r=0, t=36, b=0), height=320,
                        )
                        st.plotly_chart(_sev_fig, width='stretch')

                    # Scatter: cluster positions on unit
                    with _tp2:
                        _sc_fig = _px2.scatter(
                            _ct_summary, x='X (mm)', y='Y (mm)',
                            size='Defects', color='Severity ▼',
                            color_continuous_scale='OrRd',
                            title='Cluster Positions on Unit',
                            hover_data=['Cluster', 'Top Type', 'Buildup Layers', 'Units Affected'],
                            text='Cluster',
                        )
                        _sc_fig.update_traces(textposition='top center')
                        _sc_fig.update_layout(
                            plot_bgcolor='#000000', paper_bgcolor='#000000',
                            font=dict(color='#cccccc'),
                            coloraxis_showscale=False,
                            xaxis=dict(showgrid=False, zeroline=False, showticklabels=True,
                                       title='X (mm)', color='#aaa'),
                            yaxis=dict(showgrid=False, zeroline=False, showticklabels=True,
                                       title='Y (mm)', color='#aaa', scaleanchor='x', scaleratio=1),
                            margin=dict(l=0, r=0, t=36, b=0), height=320,
                        )
                        # Draw unit boundary if we have dimensions
                        if _ct_aligned and _ct_cell_w and _ct_cell_h:
                            _sc_fig.add_shape(
                                type="rect", x0=0, y0=0, x1=_ct_cell_w, y1=_ct_cell_h,
                                line=dict(color="rgba(0,220,130,0.5)", width=1, dash="dot"),
                                fillcolor="rgba(0,0,0,0)",
                            )
                        st.plotly_chart(_sc_fig, width='stretch')

            except ImportError:
                st.warning("scikit-learn required: pip install scikit-learn")
