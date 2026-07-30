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

from core.state import init_state
init_state()


@st.cache_resource
def _startup_cache_maintenance():
    """Prune the on-disk render cache once per server process.

    Renders persist across restarts: entries are keyed by archive digest +
    CACHE_VERSION (core/cache.py), so a render-code change makes old entries
    unreachable rather than wrongly served — pruning ages them out and caps
    total size. The sidebar's "Clear All Cache" button remains the manual wipe.
    """
    try:
        from core.cache import prune_render_cache
        prune_render_cache()
    except Exception:
        pass
    return True


_startup_cache_maintenance()

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
        from core.scope import available_verifications, default_side

        _all_verif_codes = available_verifications(aoi)

        if 'scope_bu_sel' not in st.session_state:
            st.session_state['scope_bu_sel'] = list(aoi.buildup_numbers)
        if 'scope_side_sel' not in st.session_state:
            st.session_state['scope_side_sel'] = default_side(aoi)
        if 'scope_panel_sel' not in st.session_state:
            st.session_state['scope_panel_sel'] = sorted(aoi.panel_ids)
        if 'scope_verif_sel' not in st.session_state:
            st.session_state['scope_verif_sel'] = list(_all_verif_codes)

        # Side is single-select; coerce anything stale back to exactly one valid side.
        # Covers a two-side list left over from the old multi-select behaviour, and a
        # side the freshly-loaded data doesn't actually contain.
        _sides_present = [
            s for s, code in (('Front', 'F'), ('Back', 'B'))
            if code in (getattr(aoi, 'sides', []) or [])
        ] or ['Front', 'Back']
        _stored_side = st.session_state.get('scope_side_sel') or []
        if len(_stored_side) != 1 or _stored_side[0] not in _sides_present:
            st.session_state['scope_side_sel'] = default_side(aoi)

        # Drop panels/codes that no longer exist (a re-upload can change both).
        st.session_state['scope_panel_sel'] = [
            p for p in st.session_state['scope_panel_sel'] if p in aoi.panel_ids
        ] or sorted(aoi.panel_ids)
        st.session_state['scope_verif_sel'] = [
            v for v in st.session_state['scope_verif_sel'] if v in _all_verif_codes
        ]

        with st.expander("🔬 Analysis Scope", expanded=True):

            # ── Panel toggles (only when multiple panels are loaded) ──────────
            _panel_ids = getattr(aoi, 'panel_ids', [])
            if len(_panel_ids) > 1:
                def _toggle_panel(pid):
                    def cb():
                        current = list(st.session_state.get('scope_panel_sel', sorted(_panel_ids)))
                        if pid in current:
                            if len(current) > 1:
                                current.remove(pid)
                        else:
                            current.append(pid)
                        st.session_state['scope_panel_sel'] = sorted(current)
                    return cb

                _pc1, _pc2 = st.columns([5, 1])
                _pc1.caption("**Panels** — click to include/exclude (multi-select):")

                def _all_panels():
                    st.session_state['scope_panel_sel'] = sorted(_panel_ids)

                _pc2.button("All", key="scope_panel_all", width="stretch",
                            on_click=_all_panels, help="Select every panel")

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

            # ── Side — single-select: exactly one side is active at a time ────
            def _set_side(side):
                def cb():
                    st.session_state['scope_side_sel'] = [side]
                return cb

            s_cols = st.columns(len(_sides_present), gap="small")
            _active_side = st.session_state['scope_side_sel'][0]
            for _si, _side in enumerate(_sides_present):
                s_cols[_si].button(
                    _side,
                    key=f"scope_side_{_side[0].lower()}",
                    type="primary" if _active_side == _side else "secondary",
                    width="stretch",
                    on_click=_set_side(_side),
                )

            # ── Verification codes — one global filter for every view ─────────
            if _all_verif_codes:
                st.divider()
                st.multiselect(
                    "Verification codes",
                    options=_all_verif_codes,
                    key="scope_verif_sel",
                    help="Which defect codes to include. Applies to every view.",
                )
                if not st.session_state['scope_verif_sel']:
                    st.warning(
                        "No verification codes selected — all views will be empty. "
                        "Pick at least one code above."
                    )

        st.session_state['buildup_filter_select'] = st.session_state.get('scope_bu_sel', aoi.buildup_numbers)
        st.session_state['panel_filter_select'] = st.session_state.get('scope_panel_sel', aoi.panel_ids)
        st.session_state['verif_filter_select'] = st.session_state.get('scope_verif_sel', _all_verif_codes)
        # Side is always exactly one value now, so 'All' is no longer reachable.
        st.session_state['side_cap_select'] = st.session_state['scope_side_sel'][0]
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
