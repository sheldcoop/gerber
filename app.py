"""
app.py — Streamlit application for ODB++ + AOI defect overlay visualization.

Orchestrates:
1. File upload (ODB++ archive + AOI Excel files)
2. ODB++ parsing → layer polygons
3. AOI data loading → defect coordinates
4. Coordinate alignment
5. Interactive Plotly overlay with sidebar controls

Run with: streamlit run app.py
"""

import streamlit as st
import pandas as pd
from alignment import _dict_to_alignment_result, compute_alignment_cached, apply_alignment_cached, compute_dataframe_hash

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="ODB++ + AOI Overlay",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Core Imports and Initialization
# ---------------------------------------------------------------------------

from core.state import init_state, sync_layers_to_aoi
init_state()

from ui.sidebar import handle_bg_render_polling, render_sidebar
from views.panel_overview import render_panel_overview
from views.unit_commonality import render_unit_commonality
from views.panel_heatmap import render_panel_heatmap
from views.cluster_triage import render_cluster_triage
from views.panelization_data import render_panelization_data

handle_bg_render_polling()
render_sidebar()

# ---------------------------------------------------------------------------
# Main visualization area
# ---------------------------------------------------------------------------

parsed = st.session_state.get('parsed_odb')
aoi = st.session_state.get('aoi_dataset')

if st.session_state.get('data_loaded') and (parsed or aoi):
    align_args = st.session_state.get('align_args', {})
    
    if parsed and aoi and parsed.layers and aoi.has_data:
        # Compute file hash for caching key
        _aoi_hash = compute_dataframe_hash(aoi.all_defects)
        _fids_g = tuple(tuple(f) for f in parsed.fiducials) if parsed.fiducials else None

        alignment_dict = compute_alignment_cached(
            gerber_bounds=parsed.board_bounds,
            aoi_bounds=aoi.coord_bounds,
            aoi_data_hash=_aoi_hash,
            fiducials_gerber=_fids_g,
            origin_x=parsed.origin_x,
            origin_y=parsed.origin_y,
            flip_y=align_args.get('flip_y', False),
            manual_offset_x=align_args.get('manual_offset_x', 0.0),
            manual_offset_y=align_args.get('manual_offset_y', 0.0),
            _aoi_df=aoi.all_defects,
        )
        alignment = _dict_to_alignment_result(alignment_dict)

        defect_df = apply_alignment_cached(
            _df_hash=_aoi_hash,
            alignment_dict=alignment_dict,
            unit_row=align_args.get('unit_row'),
            unit_col=align_args.get('unit_col'),
            _df=aoi.all_defects,
        )
        st.session_state['alignment_result'] = alignment
        st.session_state['last_alignment_result'] = alignment
    elif aoi and aoi.has_data:
        defect_df = aoi.all_defects.copy()
        _d_off_x = align_args.get('manual_offset_x', 0.0)
        _d_off_y = align_args.get('manual_offset_y', 0.0)
        if 'X_MM' not in defect_df.columns and 'X' in defect_df.columns:
            defect_df['X_MM'] = defect_df['X'] / 1000.0
            defect_df['Y_MM'] = defect_df['Y'] / 1000.0
        defect_df['ALIGNED_X'] = (defect_df['X_MM'] if 'X_MM' in defect_df.columns else 0.0) + _d_off_x
        defect_df['ALIGNED_Y'] = (defect_df['Y_MM'] if 'Y_MM' in defect_df.columns else 0.0) + _d_off_y
        alignment = None
    else:
        # Default empty DataFrame if no AOI uploaded but SVGs are present
        defect_df = pd.DataFrame(columns=['ALIGNED_X', 'ALIGNED_Y'])
        alignment = None

    if parsed and parsed.unknown_symbols:
        st.warning(f"⚠️ Unknown symbol types skipped: {', '.join(parsed.unknown_symbols)} — geometry may be incomplete")


    # ── View Mode Tab Bar (very top of canvas) ───────────────────────────────
    if '_view_mode' not in st.session_state:
        st.session_state['_view_mode'] = "🔭 Panel Overview"
    if st.session_state.get('_pending_view'):
        st.session_state['_view_mode'] = st.session_state.pop('_pending_view')

    _tabs = ["🔭 Panel Overview", "🗺️ Unit Commonality", "🔬 Cluster Triage", "🔥 Panel Heatmap", "📊 Panelization Data"]
    _tab_cols = st.columns(len(_tabs), gap="small")
    for _i, _label in enumerate(_tabs):
        _is_active = (st.session_state['_view_mode'] == _label)
        def _switch_view(_l=_label):
            st.session_state['_view_mode'] = _l
        _tab_cols[_i].button(
            _label,
            key=f"view_tab_{_i}",
            type="primary" if _is_active else "secondary",
            width="stretch",
            on_click=_switch_view,
        )
    st.divider()

    # --- Analysis Scope: Capsule Toggle Buttons (AOI Excel data only) ---
    if aoi and aoi.has_data:
        if 'scope_bu_sel' not in st.session_state:
            st.session_state['scope_bu_sel'] = list(aoi.buildup_numbers)
        if 'scope_side_sel' not in st.session_state:
            st.session_state['scope_side_sel'] = ['Front', 'Back']

        with st.expander("🔬 Analysis Scope", expanded=True):
            bu_labels = [f"BU-{int(b):02d}" for b in aoi.buildup_numbers]
            if bu_labels:
                bu_cols = st.columns(len(bu_labels), gap="small")

                def _toggle_bu(num):
                    def cb():
                        current = list(st.session_state.get('scope_bu_sel', list(aoi.buildup_numbers)))
                        if num in current:
                            if len(current) > 1:
                                current.remove(num)
                        else:
                            current.append(num)
                        st.session_state['scope_bu_sel'] = sorted(current)
                    return cb

                for i, (bu_num, bu_lbl) in enumerate(zip(aoi.buildup_numbers, bu_labels)):
                    is_sel = bu_num in st.session_state['scope_bu_sel']
                    bu_cols[i].button(
                        bu_lbl,
                        key=f"scope_bu_{bu_num}",
                        type="primary" if is_sel else "secondary",
                        width="stretch",
                        on_click=_toggle_bu(bu_num),
                    )

            s_cols = st.columns(2, gap="small")

            def _toggle_side(side):
                def cb():
                    current = list(st.session_state.get('scope_side_sel', ['Front', 'Back']))
                    if side in current:
                        if len(current) > 1:
                            current.remove(side)
                    else:
                        current.append(side)
                    st.session_state['scope_side_sel'] = current
                return cb

            is_front = 'Front' in st.session_state['scope_side_sel']
            is_back  = 'Back'  in st.session_state['scope_side_sel']
            s_cols[0].button("Front", key="scope_side_f", type="primary" if is_front else "secondary",
                             width="stretch", on_click=_toggle_side("Front"))
            s_cols[1].button("Back",  key="scope_side_b", type="primary" if is_back  else "secondary",
                             width="stretch", on_click=_toggle_side("Back"))

        st.session_state['buildup_filter_select'] = st.session_state.get('scope_bu_sel', aoi.buildup_numbers)
        active_sides = st.session_state.get('scope_side_sel', ['Front', 'Back'])
        if set(active_sides) == {'Front', 'Back'}:
            st.session_state['side_cap_select'] = 'All'
        elif 'Front' in active_sides:
            st.session_state['side_cap_select'] = 'Front'
        else:
            st.session_state['side_cap_select'] = 'Back'
        st.divider()

    view_mode = st.session_state['_view_mode']


    # ── Polarity helper — swap fg/bg colours in a pre-rendered SVG string ───
    _COPPER_FG  = '#b87333'
    _DRILL_FG   = '#FFD700'
    _SVG_BG     = '#060A06'
    _invert_pol = st.session_state.get('invert_polarity', False)

    def _get_svg_url(layer_obj):
        """Return SVG data URL, applying polarity inversion if toggled."""
        import base64 as _b64
        svg = layer_obj.svg_string
        if _invert_pol:
            fg = _DRILL_FG if layer_obj.layer_type == 'drill' else _COPPER_FG
            _t = '__PS__'
            svg = (svg
                   .replace(fg, _t)
                   .replace(_SVG_BG, fg)
                   .replace(_t, _SVG_BG))
        return 'data:image/svg+xml;base64,' + _b64.b64encode(svg.encode()).decode()

    if view_mode == "🔭 Panel Overview":
        render_panel_overview(parsed, aoi, align_args)

    elif view_mode == "🗺️ Unit Commonality":
        render_unit_commonality(parsed, aoi, align_args, _get_svg_url)

    elif view_mode == "🔥 Panel Heatmap":
        render_panel_heatmap(parsed, aoi, align_args)

    elif view_mode == "🔬 Cluster Triage":
        render_cluster_triage(parsed, aoi, align_args)

    elif view_mode == "📊 Panelization Data":
        render_panelization_data(parsed, aoi, align_args)

    # ---- Defect Summary Panel ----
    if aoi and aoi.has_data:
        with st.expander("📊 Defect Summary", expanded=False):
            import plotly.express as _px

            _ds_df = aoi.all_defects
            _sc1, _sc2, _sc3 = st.columns(3)
            _sc1.metric("Total Defects", f"{len(_ds_df):,}")
            _sc2.metric("Defect Types", len(aoi.defect_types))
            _sc3.metric("Buildup Layers", len(aoi.buildup_numbers))
            st.divider()

            _dc1, _dc2 = st.columns(2)

            # Bar: defect count by type
            with _dc1:
                _by_type = (
                    _ds_df.groupby('DEFECT_TYPE', observed=True)
                    .size().reset_index(name='Count')
                    .sort_values('Count', ascending=True)
                )
                _bar_fig = _px.bar(
                    _by_type, x='Count', y='DEFECT_TYPE', orientation='h',
                    title='Defects by Type',
                    color='Count', color_continuous_scale='Reds',
                )
                _bar_fig.update_layout(
                    plot_bgcolor='#000000', paper_bgcolor='#000000',
                    font=dict(color='#cccccc'), showlegend=False,
                    coloraxis_showscale=False, margin=dict(l=0, r=0, t=36, b=0), height=320,
                )
                st.plotly_chart(_bar_fig, width='stretch')

            # Donut: front vs back
            with _dc2:
                _by_side = _ds_df.groupby('SIDE', observed=True).size().reset_index(name='Count')
                _donut_fig = _px.pie(
                    _by_side, values='Count', names='SIDE',
                    title='Front vs Back', hole=0.55,
                    color_discrete_sequence=['#00c87a', '#e05050'],
                )
                _donut_fig.update_layout(
                    plot_bgcolor='#000000', paper_bgcolor='#000000',
                    font=dict(color='#cccccc'), margin=dict(l=0, r=0, t=36, b=0), height=320,
                )
                st.plotly_chart(_donut_fig, width='stretch')

            # Bar: defects per buildup layer
            _by_bu = (
                _ds_df.groupby(['BUILDUP', 'SIDE'], observed=True)
                .size().reset_index(name='Count')
                .sort_values('BUILDUP')
            )
            _bu_fig = _px.bar(
                _by_bu, x='BUILDUP', y='Count', color='SIDE',
                barmode='group', title='Defects per Buildup Layer',
                color_discrete_map={'Front': '#00c87a', 'Back': '#e05050'},
            )
            _bu_fig.update_layout(
                plot_bgcolor='#000000', paper_bgcolor='#000000',
                font=dict(color='#cccccc'), margin=dict(l=0, r=0, t=36, b=0), height=280,
            )
            st.plotly_chart(_bu_fig, width='stretch')

            # Cross-table: type × buildup
            _cross = (
                _ds_df.groupby(['DEFECT_TYPE', 'BUILDUP'], observed=True)
                .size().unstack(fill_value=0)
            )
            st.caption("**Cross-table: Defect Type × Buildup**")
            st.dataframe(_cross, width='stretch')

else:
    # Landing page
    st.title("ODB++ + AOI Defect Overlay Viewer")
    st.markdown("""
    ### Getting Started

    1. **Upload an ODB++ archive** (.tgz) from InCam Pro in the sidebar
    2. **Upload AOI Excel files** (.xlsx) from Orbotech AOI
       - Filename should follow `BU-XXF` / `BU-XXB` pattern (e.g., `BU-02F.xlsx`)
       - Or manually assign buildup/side after upload
    3. Click **Load & Process** to parse and visualize

    ### Features
    - Interactive Plotly visualization with zoom, pan, and hover
    - Toggle individual ODB++ layers with opacity control
    - Filter defects by buildup, side, and type
    - Multiple marker styles and color modes
    - Coordinate alignment debug panel

    ### Supported Formats
    | Data | Format | Notes |
    |------|--------|-------|
    | PCB Design | ODB++ in .tgz | Exported from InCam Pro; mm or inch auto-detected |
    | AOI | Excel .xlsx | Coordinates in microns, converted to mm |
    """)
