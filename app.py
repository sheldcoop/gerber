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


@st.cache_resource
def _purge_caches_on_startup():
    """Start every server process with a clean slate.

    Runs once per process (cache_resource), so a fresh `streamlit run` never serves a
    stale on-disk render. AOI Excel data lives in session_state only, so it is naturally
    gone on reload too. The "Clear All Cache" button performs the same wipe on demand.
    """
    try:
        from gerber_renderer import clear_render_cache
        clear_render_cache()
    except Exception:
        pass
    try:
        st.cache_data.clear()
    except Exception:
        pass
    return True


_purge_caches_on_startup()

from ui.sidebar import handle_bg_render_polling, render_sidebar
# Panel Overview tab disabled — kept on disk (views/panel_overview.py) but not wired up.
# from views.panel_overview import render_panel_overview
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

    # NOTE: each view computes its own defect alignment from `align_args`
    # (core/data_utils._align_defects) — no global alignment pass is needed here.

    if parsed and parsed.unknown_symbols:
        st.warning(f"⚠️ Unknown symbol types skipped: {', '.join(parsed.unknown_symbols)} — geometry may be incomplete")


    # ── View Mode Tab Bar (very top of canvas) ───────────────────────────────
    if '_view_mode' not in st.session_state:
        st.session_state['_view_mode'] = "🗺️ Unit Commonality"
    if st.session_state.get('_pending_view'):
        st.session_state['_view_mode'] = st.session_state.pop('_pending_view')

    # Panel Overview tab disabled.
    _tabs = ["🗺️ Unit Commonality", "🔬 Cluster Triage", "🔥 Panel Heatmap", "📊 Panelization Data"]
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
        if 'scope_panel_sel' not in st.session_state:
            # Default to first panel only — panel selector is single-select (radio).
            _default_panel = sorted(aoi.panel_ids)[:1] if aoi.panel_ids else []
            st.session_state['scope_panel_sel'] = _default_panel

        with st.expander("🔬 Analysis Scope", expanded=True):

            # ── Panel toggles (only when multiple panels are loaded) ──────────
            _panel_ids = getattr(aoi, 'panel_ids', [])
            if len(_panel_ids) > 1:
                def _toggle_panel(pid):
                    def cb():
                        # Radio-style: selecting a panel makes it the ONLY active one.
                        # Never show two panels' defects overlaid — user checks one at a time.
                        st.session_state['scope_panel_sel'] = [pid]
                    return cb

                p_cols = st.columns(min(len(_panel_ids), 8), gap="small")
                for _pi, _pid in enumerate(sorted(_panel_ids)):
                    # Show the actual panel number from the ID (Panel_30 → P30, Panel_01 → P01)
                    _num = _pid.split('_')[-1].split('-')[-1]
                    _plbl = f"P{int(_num):02d}" if _num.isdigit() else _pid
                    _psel = _pid in st.session_state.get('scope_panel_sel', _panel_ids)
                    p_cols[_pi % 8].button(
                        _plbl,
                        key=f"scope_panel_{_pi}",
                        help=_pid,
                        type="primary" if _psel else "secondary",
                        width="stretch",
                        on_click=_toggle_panel(_pid),
                    )
                st.divider()

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
        st.session_state['panel_filter_select'] = st.session_state.get('scope_panel_sel', aoi.panel_ids)
        active_sides = st.session_state.get('scope_side_sel', ['Front', 'Back'])
        if set(active_sides) == {'Front', 'Back'}:
            st.session_state['side_cap_select'] = 'All'
        elif 'Front' in active_sides:
            st.session_state['side_cap_select'] = 'Front'
        else:
            st.session_state['side_cap_select'] = 'Back'
        st.divider()

    view_mode = st.session_state['_view_mode']


    # Panel Overview tab disabled.
    # if view_mode == "🔭 Panel Overview":
    #     render_panel_overview(parsed, aoi, align_args)

    if view_mode == "🗺️ Unit Commonality":
        render_unit_commonality(parsed, aoi, align_args)

    elif view_mode == "🔥 Panel Heatmap":
        render_panel_heatmap(parsed, aoi, align_args)

    elif view_mode == "🔬 Cluster Triage":
        render_cluster_triage(parsed, aoi, align_args)

    elif view_mode == "📊 Panelization Data":
        render_panelization_data(parsed, aoi, align_args)

else:
    # Landing page
    st.title("ODB++ + AOI Defect Overlay Viewer")
    st.markdown("""
    ### Getting Started

    1. **Upload an ODB++ archive** (.tgz) from InCam Pro in the sidebar
    2. **Upload AOI Excel files** (.xlsx) from Orbotech AOI
       - **Recommended naming:** `BU_{buildup}{side}_Panel{panel}_S{section}.xlsx`
       - `BU_01F` = Buildup 1, Front side
       - `Panel1` = Panel number (same number groups files for the same panel run)
       - `S1`, `S2`, `S3` = AOI scan sections covering the same panel side (merged automatically)
       - **Example — 2 panels, BU01 Front, scanned in 3 sections each:**
         ```
         BU_01F_Panel1_S1.xlsx   BU_01F_Panel2_S1.xlsx
         BU_01F_Panel1_S2.xlsx   BU_01F_Panel2_S2.xlsx
         BU_01F_Panel1_S3.xlsx   BU_01F_Panel2_S3.xlsx
         ```
       - Legacy format `BU-02F.xlsx` still supported (treated as Panel 1, Section 1)
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
