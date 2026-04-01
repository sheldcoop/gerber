import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from alignment import get_panel_quadrant_bounds, FRAME_WIDTH, FRAME_HEIGHT
from visualizer import build_defect_only_figure, OverlayConfig
from gerber_renderer import save_render_cache
from core.data_utils import compute_panel_shapes, compute_clusters_cached

def render_panel_overview(parsed, aoi, align_args):
    st.markdown("### Panel Defect Map")
    if aoi and aoi.has_data:
        panel_df = aoi.all_defects.copy()
        _p_off_x = align_args.get('manual_offset_x', 0.0)
        _p_off_y = align_args.get('manual_offset_y', 0.0)
        if 'X_MM' not in panel_df.columns and 'X' in panel_df.columns:
            panel_df['ALIGNED_X'] = panel_df['X'] / 1000.0 + _p_off_x
            panel_df['ALIGNED_Y'] = panel_df['Y'] / 1000.0 + _p_off_y
        else:
            panel_df['ALIGNED_X'] = (panel_df['X_MM'] if 'X_MM' in panel_df.columns else 0.0) + _p_off_x
            panel_df['ALIGNED_Y'] = (panel_df['Y_MM'] if 'Y_MM' in panel_df.columns else 0.0) + _p_off_y

        if align_args.get('flip_y', False) and not panel_df.empty:
            panel_df['ALIGNED_Y'] = panel_df['ALIGNED_Y'].max() - panel_df['ALIGNED_Y']

        panel_config = OverlayConfig(min_feature_size=0.1)  # LOD: suppress sub-0.1mm traces at panel zoom

        # Quadrant grid geometry (for grid overlay lines only)
        quad_bounds = get_panel_quadrant_bounds(
            st.session_state.get('quad_rows_input', 6),
            st.session_state.get('quad_cols_input', 6),
            dyn_gap_x=st.session_state.get('dyn_gap_x_input', 5.0),
            dyn_gap_y=st.session_state.get('dyn_gap_y_input', 3.5),
        )

        # Viewport: use ODB++ panel frame when TGZ is loaded (same space as CAM PNG
        # and AOI X_MM/Y_MM). Fall back to geometry-engine bounds when no TGZ.
        _rp_for_bounds = st.session_state.get('rendered_odb')
        if _rp_for_bounds and _rp_for_bounds.panel_layout:
            _vpw = _rp_for_bounds.panel_layout.panel_width
            _vph = _rp_for_bounds.panel_layout.panel_height
            panel_config.board_bounds = (-10, -10, _vpw + 10, _vph + 10)
        else:
            ax1, ay1, ax2, ay2 = quad_bounds['frame']
            panel_config.board_bounds = (ax1 - 10, ay1 - 10, ax2 + 10, ay2 + 10)

        # Read active scope filters from capsule UI
        panel_config.color_mode    = st.session_state.get('color_mode_select', 'by_type')
        panel_config.marker_style  = st.session_state.get('marker_style_select', 'dot')
        panel_config.defect_types  = st.session_state.get('defect_type_select', aoi.defect_types)
        panel_config.buildup_filter = st.session_state.get('buildup_filter_select', aoi.buildup_numbers)
        side_active = st.session_state.get('side_cap_select', 'All')
        panel_config.side_filter   = 'Both' if side_active == 'All' else side_active

        panel_fig = build_defect_only_figure(panel_df, panel_config)
        panel_fig.update_layout(showlegend=False)

        # ── CAM (Gerbonara) background tiling (pre-cached panel SVG) ────
        _rendered_panel = st.session_state.get('rendered_odb')
        if _rendered_panel and _rendered_panel.panel_layout:
            # Pick which layer to display (first checked layer only — if none checked, show nothing)
            _panel_svg_url = None
            _panel_bg_name = None
            _want_layer = None
            _all_checked_panel = [
                _ln for _ln in _rendered_panel.layers
                if st.session_state.get(f"vis_{_ln}", False)
            ]
            if _all_checked_panel:
                _want_layer = _all_checked_panel[0]   # Background can only show one layer
            # Build panel SVG only for the selected layer (on-demand, one layer at a time)
            if _want_layer:
                _want_lyr_obj = _rendered_panel.layers[_want_layer]
                if not _want_lyr_obj.panel_svg_data_url:
                    with st.spinner(f"Building panel image for {_want_layer}..."):
                        from gerber_renderer import build_panel_svg
                        try:
                            _want_lyr_obj.panel_svg_data_url = build_panel_svg(
                                _want_lyr_obj.svg_string, _rendered_panel.panel_layout
                            )
                        except Exception:
                            pass
                        _tgz_b = st.session_state.get('_tgz_bytes_for_cache')
                        _tgz_d = st.session_state.get('_tgz_digest')
                        if (_tgz_b or _tgz_d) and _want_lyr_obj.panel_svg_data_url:
                            save_render_cache(_rendered_panel, digest=_tgz_d, tgz_bytes=_tgz_b if not _tgz_d else None)
                if _want_lyr_obj.panel_svg_data_url:
                    _panel_svg_url = _want_lyr_obj.panel_svg_data_url
                    _panel_bg_name = _want_layer

            if _panel_svg_url:
                panel_fig.update_layout(images=[dict(
                    source=_panel_svg_url,
                    xref="x", yref="y",
                    x=0, y=FRAME_HEIGHT,
                    sizex=FRAME_WIDTH, sizey=FRAME_HEIGHT,
                    sizing="stretch", layer="below", opacity=1.0,
                )])
                _extra = len(_all_checked_panel) - 1
                if _extra > 0:
                    st.caption(
                        f"Panel image: **{_want_layer}**"
                        f" (+ {_extra} more selected — panel view shows one layer at a time)"
                    )

        # ── Cluster Intelligence Overlay ──────────────────────────────
        if not panel_df.empty and 'ALIGNED_X' in panel_df.columns and len(panel_df) >= 3:
            from alignment import compute_dataframe_hash
            _cl_hash = compute_dataframe_hash(panel_df)
            clustered_df, cluster_summary, _hulls = compute_clusters_cached(
                _cl_hash, panel_df, eps=2.0, min_samples=3
            )
            if not cluster_summary.empty:
                for _cid, (_hull, _cnt) in _hulls.items():
                    hx, hy = _hull
                    panel_fig.add_trace(go.Scatter(
                        x=hx, y=hy, mode='lines',
                        line=dict(color='#00FFCC', width=2, dash='dash'),
                        name=f"Cluster {_cid} ({_cnt})",
                        hoverinfo='name', showlegend=False,
                    ))
                st.session_state['_cluster_summary'] = cluster_summary
                st.session_state['_clustered_df'] = clustered_df

        # ── Professional PCB substrate panel background ───────────────
        # Use ODB++ panel dimensions when TGZ is loaded (matches CAM PNG exactly).
        # Fall back to geometry-engine frame bounds when no TGZ.
        if _rp_for_bounds and _rp_for_bounds.panel_layout:
            frame_bx1, frame_by1 = 0.0, 0.0
            frame_bx2 = _rp_for_bounds.panel_layout.panel_width
            frame_by2 = _rp_for_bounds.panel_layout.panel_height
        else:
            frame_bx1, frame_by1, frame_bx2, frame_by2 = quad_bounds['frame']

        # Dark green solder-mask base for the whole frame
        panel_fig.add_shape(
            type="rect",
            x0=frame_bx1 - 8, y0=frame_by1 - 8,
            x1=frame_bx2 + 8, y1=frame_by2 + 8,
            fillcolor="#2B3A2B",          # PCB panel carrier (dark green)
            line=dict(color="#1a2a1a", width=1),
            layer="below",
        )
        # Copper FR4 frame band
        panel_fig.add_shape(
            type="rect",
            x0=frame_bx1, y0=frame_by1,
            x1=frame_bx2, y1=frame_by2,
            fillcolor="rgba(184,115,51,0.18)",   # warm copper tint
            line=dict(color="#C87533", width=3),
            layer="below",
        )

        # Unit cell grid — subtle outlines for each individual PCB
        for name, (bx1, by1, bx2, by2) in quad_bounds.items():
            if name == 'frame':
                continue
            # quadrant zone shading
            panel_fig.add_shape(
                type="rect",
                x0=bx1, y0=by1, x1=bx2, y1=by2,
                fillcolor="rgba(0,200,120,0.04)",
                line=dict(color="rgba(0,200,120,0.35)", width=1, dash="dot"),
                layer="below",
            )

        # Draw individual unit cells (cached — only recomputes on grid param change)
        _pq_rows = int(st.session_state.get('quad_rows_input', 6))
        _pq_cols = int(st.session_state.get('quad_cols_input', 6))
        _pd_gap_x = float(st.session_state.get('dyn_gap_x_input', 5.0))
        _pd_gap_y = float(st.session_state.get('dyn_gap_y_input', 3.5))
        _cell_shapes = compute_panel_shapes(_pq_rows, _pq_cols, _pd_gap_x, _pd_gap_y)
        panel_fig.update_layout(shapes=panel_fig.layout.shapes + tuple(_cell_shapes))

        event = st.plotly_chart(
            panel_fig,
            width='stretch',
            on_select="rerun",
            selection_mode="points",
            key="panel_map_selection",
            config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False},
        )

        # --- Click event handling ---
        sel = event.selection if (event and hasattr(event, 'selection')) else {}
        point_indices = sel.get('point_indices', [])
        if point_indices:
            clicked_row = panel_df.iloc[point_indices[0]]
            ux = clicked_row.get('UNIT_INDEX_X')
            uy = clicked_row.get('UNIT_INDEX_Y')

            if ux is not None and uy is not None:
                st.rerun()

    elif st.session_state.get('rendered_odb') and st.session_state.get('bg_source') == 'CAM (Gerbonara)':
        # CAM-only mode: no AOI data, but TGZ is rendered — show tiled panel SVG
        _rodb = st.session_state['rendered_odb']
        _pl_cam = _rodb.panel_layout
        if _pl_cam and _rodb.layers:
            _cam_fig = go.Figure()
            _cam_fig.update_layout(
                xaxis=dict(range=[-10, FRAME_WIDTH + 10], scaleanchor='y', scaleratio=1,
                           showgrid=False, zeroline=False, color='#aaa'),
                yaxis=dict(range=[-10, FRAME_HEIGHT + 10], showgrid=False, zeroline=False, color='#aaa'),
                plot_bgcolor='#1a2a1a', paper_bgcolor='#111a11',
                margin=dict(l=0, r=0, t=24, b=0),
                height=720,
            )
            # Panel frame shapes
            _cam_fig.add_shape(type="rect", x0=-5, y0=-5, x1=FRAME_WIDTH+5, y1=FRAME_HEIGHT+5,
                               fillcolor="#2B3A2B", line=dict(color="#1a2a1a", width=1), layer="below")
            _cam_fig.add_shape(type="rect", x0=0, y0=0, x1=FRAME_WIDTH, y1=FRAME_HEIGHT,
                               fillcolor="rgba(184,115,51,0.18)", line=dict(color="#C87533", width=3), layer="below")
            # Unit cell outlines from TGZ positions
            _uw_cam = _pl_cam.unit_bounds[0]
            _uh_cam = _pl_cam.unit_bounds[1]
            for _px, _py in _pl_cam.unit_positions:
                _cam_fig.add_shape(type="rect", x0=_px, y0=_py,
                                   x1=_px + _uw_cam, y1=_py + _uh_cam,
                                   fillcolor="rgba(0,180,100,0.07)",
                                   line=dict(color="rgba(0,220,130,0.5)", width=0.8), layer="below")
            # Use pre-cached panel SVG for the checked layer only (no fallback)
            _panel_svg = None
            _sel_ln2 = None
            for _ln2 in _rodb.layers:
                if st.session_state.get(f"vis_{_ln2}", False):
                    _sel_ln2 = _ln2
                    break

            if _sel_ln2:
                _sel_lyr2 = _rodb.layers[_sel_ln2]
                if not _sel_lyr2.panel_svg_data_url:
                    with st.spinner(f"Building panel image for {_sel_ln2}..."):
                        from gerber_renderer import build_panel_svg
                        try:
                            _sel_lyr2.panel_svg_data_url = build_panel_svg(
                                _sel_lyr2.svg_string, _pl_cam
                            )
                        except Exception:
                            pass
                        _tgz_b2 = st.session_state.get('_tgz_bytes_for_cache')
                        _tgz_d2 = st.session_state.get('_tgz_digest')
                        if (_tgz_b2 or _tgz_d2) and _sel_lyr2.panel_svg_data_url:
                            save_render_cache(_rodb, digest=_tgz_d2, tgz_bytes=_tgz_b2 if not _tgz_d2 else None)
                _panel_svg = _sel_lyr2.panel_svg_data_url

            if not _panel_svg:
                st.caption("☝️ Select a layer in the sidebar to display the panel image.")

            if _panel_svg:
                _cam_fig.update_layout(images=[dict(
                    source=_panel_svg,
                    xref="x", yref="y",
                    x=0, y=FRAME_HEIGHT,
                    sizex=FRAME_WIDTH, sizey=FRAME_HEIGHT,
                    sizing="stretch", layer="below", opacity=1.0,
                )])
            st.plotly_chart(_cam_fig, width='stretch',
                            config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})
        else:
            st.info("Panel layout not found in TGZ. Upload AOI data for panel view.")

    else:
        st.info("Upload AOI defect data to view the Panel Map.")
