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

import re

import streamlit as st
import pandas as pd

from odb_parser import parse_odb_archive, ParsedODB
from aoi_loader import (
    load_aoi_files, load_aoi_with_manual_side, render_column_mapping_ui,
    AOIDataset, FILENAME_PATTERN,
)
from alignment import (
    compute_alignment, apply_alignment, get_debug_info, AlignmentResult,
    compute_alignment_cached, apply_alignment_cached, _dict_to_alignment_result,
    compute_dataframe_hash,
    calculate_physical_unit_origin, get_panel_quadrant_bounds,
    calculate_geometry, FRAME_WIDTH, FRAME_HEIGHT, INTER_UNIT_GAP,
)
from visualizer import build_overlay_figure, build_defect_only_figure, OverlayConfig
from svg_utils import (
    load_svg_store, parse_svg_keys, get_svg_viewbox_mm,
    svg_to_data_url, get_rounded_rect_path,
)
import plotly.graph_objects as go


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
# Session state initialization
# ---------------------------------------------------------------------------

def _init_state():
    """Initialize session state with defaults."""
    defaults = {
        'parsed_odb': None,          # ParsedODB
        'aoi_dataset': None,         # AOIDataset
        'alignment_result': None,    # AlignmentResult
        'data_loaded': False,
        'align_args': {},            # Reset on each load to prevent stale offsets
        'needs_manual_side': {},     # filename → True if BU/side not detected
        'svg_store': {},             # {"BU-01_F": svg_string, ...}
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def sync_layers_to_aoi() -> None:
    """Callback: sync ODB++ layer checkboxes to the active AOI buildup/side filter."""
    local_parsed = st.session_state.get('parsed_odb')
    if not local_parsed:
        return

    b_list = st.session_state.get('buildup_filter_select', [])
    side_str = st.session_state.get('side_filter_select', 'Both')

    for name, lyr in local_parsed.layers.items():
        if lyr.layer_type not in ('copper', 'soldermask'):
            continue
        name_upper = name.upper()
        # Only touch layers that carry a buildup number in their name
        if not any(char.isdigit() for char in name_upper):
            continue
        has_num = any(str(b) in name_upper for b in b_list)
        is_top  = 'F' in name_upper or 'TOP' in name_upper
        is_bot  = 'B' in name_upper or 'BOT' in name_upper

        visible = has_num
        if side_str == 'Front' and (is_bot or not is_top): visible = False
        if side_str == 'Back'  and (is_top or not is_bot): visible = False
        if side_str == 'Both'  and not (is_top or is_bot): visible = False

        st.session_state[f"vis_{name}"] = visible


_init_state()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("ODB++ + AOI Overlay")
    st.caption("Upload ODB++ archive and AOI defect data for overlay visualization")

    # ---- Section 1: File Upload ----
    st.header("1. Upload Files")

    gerber_file = st.file_uploader(
        "ODB++ Archive (.tgz)",
        type=['tgz', 'gz'],
        help="Compressed ODB++ archive exported from InCam Pro",
    )

    aoi_files = st.file_uploader(
        "AOI Excel Files (.xlsx)",
        type=['xlsx', 'xls'],
        accept_multiple_files=True,
        help="Orbotech AOI defect data. Filename should follow BU-XXF / BU-XXB pattern",
    )

    # Show filename-based buildup/side detection results
    if aoi_files:
        with st.expander("Detected Buildup/Side", expanded=False):
            needs_manual = {}
            for uf in aoi_files:
                match = FILENAME_PATTERN.search(uf.name)
                if match:
                    bu = int(match.group(1))
                    side = match.group(2).upper()
                    st.success(f"**{uf.name}** → BU-{bu:02d} {'Front' if side == 'F' else 'Back'}")
                else:
                    st.warning(f"**{uf.name}** → Could not detect buildup/side")
                    needs_manual[uf.name] = True
            st.session_state['needs_manual_side'] = needs_manual

    # Manual buildup/side assignment for undetected files
    manual_map = {}
    if st.session_state.get('needs_manual_side'):
        st.subheader("Manual Buildup/Side Assignment")
        for fname in st.session_state['needs_manual_side']:
            col1, col2 = st.columns(2)
            with col1:
                bu = st.number_input(f"Buildup # for {fname}", min_value=0, max_value=20, value=1, key=f"bu_{fname}")
            with col2:
                side = st.selectbox(f"Side for {fname}", ['Front', 'Back'], key=f"side_{fname}")
            manual_map[fname] = (bu, 'F' if side == 'Front' else 'B')

    st.divider()

    # ---- Load & Process Button ----
    col_btn1, col_btn2 = st.columns([3, 1])
    with col_btn1:
        load_btn = st.button("🔄 Load & Process", width='stretch', type="primary")
    with col_btn2:
        test_btn = st.button("🧪 Auto-Load Test", width='stretch')

    if load_btn or test_btn:
        parsed_odb = None
        aoi_dataset = None

        if test_btn:
            import io
            import os
            import sys
            import pandas as pd
            
            # 1. Load Dummy ODB++ Board
            if os.path.exists("test_2F.tgz"):
                with open("test_2F.tgz", "rb") as f:
                    gerber_file = io.BytesIO(f.read())
                    gerber_file.name = "test_2F.tgz"
                    
            # 2. Native Dynamic Execution of faster-aoi Sample Generator
            faster_dir = "/Users/prince/Desktop/faster-aoi"
            if os.path.exists(faster_dir) and faster_dir not in sys.path:
                sys.path.insert(0, faster_dir)
                
            try:
                from src.io.sample_generator import generate_sample_data
                p_data = generate_sample_data(6, 6, 510.0, 515.0, 13.0, 10.0)
                dfs = []
                buildups = set()
                dtypes = set()
                for l_num in p_data.get_all_layer_nums():
                    for side in p_data.get_sides_for_layer(l_num):
                        layer = p_data.get_layer(l_num, side)
                        if layer and not layer.data.empty:
                            df = layer.data.copy()
                            df['BUILDUP'] = layer.layer_num
                            buildups.add(layer.layer_num)
                            dtypes.update(df['DEFECT_TYPE'].unique())
                            dfs.append(df)
                    
                final_df = pd.concat(dfs, ignore_index=True)
                # Map columns to match what the visualizer expects
                final_df['X'] = final_df['X_COORDINATES']
                final_df['Y'] = final_df['Y_COORDINATES']
                final_df['X_MM'] = final_df['X_COORDINATES'] / 1000.0
                final_df['Y_MM'] = final_df['Y_COORDINATES'] / 1000.0
                
                aoi_dataset = AOIDataset(
                    all_defects=final_df,
                    buildup_numbers=sorted(list(buildups)),
                    defect_types=sorted(list(dtypes)),
                    sides=sorted(list(final_df['SIDE'].unique()))
                )
                aoi_files = None # Skip file loader
            except Exception as e:
                st.error(f"Failed to load faster-aoi sample logic: {e}")

        # Parse ODB++ archive
        if gerber_file:
            with st.spinner("Parsing ODB++ archive..."):
                try:
                    parsed_odb = parse_odb_archive(
                        gerber_file.read(), gerber_file.name
                    )
                    gerber_file.seek(0)
                    st.session_state['parsed_odb'] = parsed_odb

                    if parsed_odb.layers:
                        st.success(
                            f"Parsed {len(parsed_odb.layers)} ODB++ layers "
                            f"(step: {parsed_odb.step_name}, units: {parsed_odb.units})"
                        )
                    else:
                        st.error("No layers parsed from ODB++ archive")

                    for w in parsed_odb.warnings[:10]:
                        st.warning(w, icon="⚠️")
                except Exception as e:
                    st.error(f"ODB++ parsing failed: {e}")

        # Load AOI data
        if aoi_files:
            with st.spinner("Loading AOI defect data..."):
                try:
                    if manual_map:
                        aoi_dataset = load_aoi_with_manual_side(aoi_files, manual_map)
                    else:
                        aoi_dataset = load_aoi_files(aoi_files)

                    st.session_state['aoi_dataset'] = aoi_dataset

                    if aoi_dataset.has_data:
                        st.success(
                            f"Loaded {len(aoi_dataset.all_defects)} defects "
                            f"({len(aoi_dataset.defect_types)} types, "
                            f"{len(aoi_dataset.buildup_numbers)} buildups)"
                        )
                    else:
                        st.error("No valid defect data loaded")

                    for w in aoi_dataset.warnings[:10]:
                        st.warning(w, icon="⚠️")
                except Exception as e:
                    st.error(f"AOI loading failed: {e}")
        elif aoi_dataset and aoi_dataset.has_data:
            st.session_state['aoi_dataset'] = aoi_dataset
            st.success(
                f"Synthetic Data Active: {len(aoi_dataset.all_defects)} defects "
                f"({len(aoi_dataset.defect_types)} types, "
                f"{len(aoi_dataset.buildup_numbers)} buildups)"
            )

        st.session_state['data_loaded'] = True

    if st.session_state.get('data_loaded'):
        with st.sidebar:
            st.divider()
            st.header("Coordinate Alignment")

            # Explicit Grid definition to govern the mathematical unit cell width
            st.subheader("Physical Panel Layout")
            col_g1, col_g2 = st.columns(2)
            with col_g1:
                quad_rows = st.number_input("Rows per Quadrant", min_value=1, value=6, step=1)
            with col_g2:
                quad_cols = st.number_input("Cols per Quadrant", min_value=1, value=6, step=1)

            # Dynamic inter-quadrant gaps (user-given, per faster-aoi convention)
            col_g3, col_g4 = st.columns(2)
            with col_g3:
                dyn_gap_x = st.number_input("Dyn Gap X (mm)", min_value=0.0, value=5.0, step=0.5,
                                            key='dyn_gap_x_input',
                                            help="Dynamic inter-quadrant gap in X. Total gap = 3 + 2×this.")
            with col_g4:
                dyn_gap_y = st.number_input("Dyn Gap Y (mm)", min_value=0.0, value=3.5, step=0.5,
                                            key='dyn_gap_y_input',
                                            help="Dynamic inter-quadrant gap in Y. Total gap = 3 + 2×this.")

            flip_y = st.checkbox("Flip Y axis", False, help="Invert Y coordinates based on board height")
            
            aoi_data = st.session_state.get('aoi_dataset')
            valid_rows, valid_cols = [], []
        
            has_y = False
            has_x = False
            if aoi_data and aoi_data.has_data:
                has_y = 'UNIT_INDEX_Y' in aoi_data.all_defects.columns
                has_x = 'UNIT_INDEX_X' in aoi_data.all_defects.columns
            
            if has_y:
                valid_rows = sorted(list(aoi_data.all_defects['UNIT_INDEX_Y'].dropna().unique()))
            else:
                valid_rows = list(range(quad_rows * 2))
            
            if has_x:
                valid_cols = sorted(list(aoi_data.all_defects['UNIT_INDEX_X'].dropna().unique()))
            else:
                valid_cols = list(range(quad_cols * 2))
        
            unit_row_opts = ['All'] + [int(r) for r in valid_rows]
            unit_col_opts = ['All'] + [int(c) for c in valid_cols]

            if 'manual_offset_x' not in st.session_state:
                st.session_state['manual_offset_x'] = 0.0
            if 'manual_offset_y' not in st.session_state:
                st.session_state['manual_offset_y'] = 0.0

            def update_offsets():
                row_val = st.session_state.get('sel_unit_row', 'All')
                col_val = st.session_state.get('sel_unit_col', 'All')
                if row_val != 'All' and col_val != 'All':
                    ox, oy = calculate_physical_unit_origin(
                        int(row_val), int(col_val),
                        panel_rows_per_quad=st.session_state.get('quad_rows_input', 6),
                        panel_cols_per_quad=st.session_state.get('quad_cols_input', 6),
                        dyn_gap_x=st.session_state.get('dyn_gap_x_input', 5.0),
                        dyn_gap_y=st.session_state.get('dyn_gap_y_input', 3.5),
                    )
                    st.session_state['manual_offset_x'] = float(round(ox, 3))
                    st.session_state['manual_offset_y'] = float(round(oy, 3))

            # --- Phase 2 FIX: consume pending click-to-inspect navigation BEFORE
            # the selectbox widgets are instantiated. Writing to a widget-bound key
            # after the widget renders raises StreamlitAPIException, so we do it here.
            if st.session_state.get('_pending_nav'):
                prow = st.session_state.pop('_pending_row', None)
                pcol = st.session_state.pop('_pending_col', None)
                st.session_state.pop('_pending_nav')
                if prow is not None and prow in unit_row_opts:
                    st.session_state['sel_unit_row'] = prow
                if pcol is not None and pcol in unit_col_opts:
                    st.session_state['sel_unit_col'] = pcol

            col3, col4 = st.columns(2)
            with col3:
                unit_row = st.selectbox("Unit Row", unit_row_opts, key='sel_unit_row', on_change=update_offsets)
                # Link the number input value to session state so the callback sees it
                st.session_state['quad_rows_input'] = quad_rows
            with col4:
                unit_col = st.selectbox("Unit Col", unit_col_opts, key='sel_unit_col', on_change=update_offsets)
                st.session_state['quad_cols_input'] = quad_cols
            
            unit_row_val = None if unit_row == 'All' else unit_row
            unit_col_val = None if unit_col == 'All' else unit_col

            col1, col2 = st.columns(2)
            with col1:
                offset_x = st.number_input("X Offset (mm)", step=0.1, key='manual_offset_x')
            with col2:
                offset_y = st.number_input("Y Offset (mm)", step=0.1, key='manual_offset_y')

            st.session_state['align_args'] = {
                'flip_y': flip_y,
                'manual_offset_x': offset_x,
                'manual_offset_y': offset_y,
                'unit_row': unit_row_val,
                'unit_col': unit_col_val,
            }

        st.divider()

        # ---- Section 2: Layer Controls ----
        parsed = st.session_state.get('parsed_odb')
        if parsed and parsed.layers:
            st.header("2. Layer Controls")

            visible_layers = []
            layer_opacities = {}

            for layer_name, layer in parsed.layers.items():
                # Default visibility: ON for copper / soldermask / profile; OFF for everything else
                default_visible = layer.layer_type in ('copper', 'soldermask', 'outline')

                default_opacity = 0.40

                col1, col2 = st.columns([1, 2])
                with col1:
                    visible = st.checkbox(
                        layer_name,
                        value=default_visible,
                        key=f"vis_{layer_name}",
                        help=f"{layer.layer_type} — {layer.polygon_count} shapes",
                    )
                with col2:
                    opacity = st.slider(
                        "Opacity",
                        0.0, 1.0, default_opacity,
                        step=0.05,
                        key=f"opacity_{layer_name}",
                        label_visibility="collapsed",
                    )

                if visible:
                    visible_layers.append(layer_name)
                layer_opacities[layer_name] = opacity

            st.divider()

        # ---- Section 3: Defect Filters ----
        aoi = st.session_state.get('aoi_dataset')
        if aoi and aoi.has_data:
            # We keep only Display Options in the navigation rail
            st.header("3. Display Options")
            marker_style = st.selectbox(
                "Marker Style",
                ['dot', 'crosshair', 'x_mark'],
                format_func=lambda x: x.replace('_', ' ').title(),
                key='marker_style_select',
            )

            color_mode = st.selectbox(
                "Color Mode",
                ['by_type', 'by_buildup', 'by_severity'],
                format_func=lambda x: x.replace('_', ' ').title(),
                key='color_mode_select',
            )

        # ---- SVG Upload (Experimental) ----
        with st.expander("🧪 SVG Layers (Experimental)", expanded=False):
            st.caption("Upload one SVG per buildup per side. Naming: `BU-01_F.svg`, `BU-02_B.svg` …")
            svg_uploads = st.file_uploader(
                "SVG files",
                type=["svg"],
                accept_multiple_files=True,
                key="svg_uploads",
                label_visibility="collapsed",
            )
            if svg_uploads:
                new_store = load_svg_store(svg_uploads)
                if new_store:
                    st.session_state['svg_store'] = new_store
                    bu_nums, sides = parse_svg_keys(new_store)
                    st.success(f"Loaded {len(new_store)} SVG(s): BU-{bu_nums} × {sides}")
                    # Try to read viewBox dims; show calibration input if not found
                    first_key = next(iter(new_store))
                    vb = get_svg_viewbox_mm(new_store[first_key])
                    if vb:
                        st.session_state['svg_cell_w'] = vb[0]
                        st.session_state['svg_cell_h'] = vb[1]
                        st.caption(f"viewBox: {vb[0]:.3f} × {vb[1]:.3f} mm (auto-detected)")
                    else:
                        st.warning("⚠️ Could not read viewBox in mm — enter unit dimensions manually.")
                        vc1, vc2 = st.columns(2)
                        w_mm = vc1.number_input("Unit width (mm)", min_value=0.1, value=35.0, step=0.1, key='svg_cell_w')
                        h_mm = vc2.number_input("Unit height (mm)", min_value=0.1, value=39.0, step=0.1, key='svg_cell_h')


# ---------------------------------------------------------------------------
# Main visualization area
# ---------------------------------------------------------------------------

parsed = st.session_state.get('parsed_odb')
aoi = st.session_state.get('aoi_dataset')

# Render main visualization if either traditional data is loaded OR SVGs are uploaded
if (st.session_state.get('data_loaded') and (parsed or aoi)) or st.session_state.get('svg_store'):
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
    elif aoi and aoi.has_data:
        defect_df = aoi.all_defects.copy()
        if 'X_MM' not in defect_df.columns and 'X' in defect_df.columns:
            defect_df['X_MM'] = defect_df['X'] / 1000.0
            defect_df['Y_MM'] = defect_df['Y'] / 1000.0
        # Fix: DataFrame.get() returns None for missing columns, not the fallback scalar
        defect_df['ALIGNED_X'] = defect_df['X_MM'] if 'X_MM' in defect_df.columns else 0.0
        defect_df['ALIGNED_Y'] = defect_df['Y_MM'] if 'Y_MM' in defect_df.columns else 0.0
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

    _svg_store = st.session_state.get('svg_store', {})
    _tabs = ["🔭 Panel Overview", "🔬 Single Unit Inspection", "🎯 Calibration Wizard"]
    if _svg_store:
        _tabs += ["🧪 SVG Unit View", "🗺️ SVG Panel View"]
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


    if view_mode == "🔭 Panel Overview":
        st.markdown("### Panel Defect Map")
        if aoi and aoi.has_data:
            panel_df = aoi.all_defects.copy()
            if 'X_MM' not in panel_df.columns and 'X' in panel_df.columns:
                panel_df['ALIGNED_X'] = panel_df['X'] / 1000.0
                panel_df['ALIGNED_Y'] = panel_df['Y'] / 1000.0
            else:
                panel_df['ALIGNED_X'] = panel_df['X_MM'] if 'X_MM' in panel_df.columns else 0.0
                panel_df['ALIGNED_Y'] = panel_df['Y_MM'] if 'Y_MM' in panel_df.columns else 0.0

            if align_args.get('flip_y', False) and not panel_df.empty:
                panel_df['ALIGNED_Y'] = panel_df['ALIGNED_Y'].max() - panel_df['ALIGNED_Y']

            panel_config = OverlayConfig(min_feature_size=0.1)  # LOD: suppress sub-0.1mm traces at panel zoom

            # Full-panel bounds for the substrate background grid
            quad_bounds = get_panel_quadrant_bounds(
                st.session_state.get('quad_rows_input', 6),
                st.session_state.get('quad_cols_input', 6),
                dyn_gap_x=st.session_state.get('dyn_gap_x_input', 5.0),
                dyn_gap_y=st.session_state.get('dyn_gap_y_input', 3.5),
            )
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

            # ── Cluster Intelligence Overlay ──────────────────────────────
            from clustering import compute_clusters, get_cluster_summary, get_cluster_hull_coords
            if not panel_df.empty and 'ALIGNED_X' in panel_df.columns and len(panel_df) >= 3:
                clustered_df = compute_clusters(panel_df, eps=2.0, min_samples=3)
                cluster_summary = get_cluster_summary(clustered_df)
                if not cluster_summary.empty:
                    # Draw convex hull boundaries for each cluster
                    for _, crow in cluster_summary.iterrows():
                        hull = get_cluster_hull_coords(clustered_df, crow['cluster_id'])
                        if hull:
                            hx, hy = hull
                            panel_fig.add_trace(go.Scatter(
                                x=hx, y=hy, mode='lines',
                                line=dict(color='#00FFCC', width=2, dash='dash'),
                                name=f"Cluster {crow['cluster_id']} ({crow['defect_count']})",
                                hoverinfo='name', showlegend=True,
                            ))
                    # Store for cluster panel below
                    st.session_state['_cluster_summary'] = cluster_summary
                    st.session_state['_clustered_df'] = clustered_df

            # Draw Substrate Structural Lines (Phase 5)
            for name, (bx1, by1, bx2, by2) in quad_bounds.items():
                is_frame = name == 'frame'
                shape_kwargs = dict(
                    type="rect",
                    x0=bx1, y0=by1, x1=bx2, y1=by2,
                    line=dict(color="rgba(184, 115, 51, 0.8)", width=4 if is_frame else 2),
                    layer="below"
                )
                if is_frame:
                    # Fix: passing fillcolor=None causes a Plotly deprecation warning
                    shape_kwargs['fillcolor'] = "rgba(184, 115, 51, 0.05)"
                panel_fig.add_shape(**shape_kwargs)

            event = st.plotly_chart(
                panel_fig,
                width='stretch',
                on_select="rerun",
                selection_mode="points",
                key="panel_map_selection",
                config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False},
            )
            
            # --- Phase 2: Click-To-Inspect ---
            # Fix: Cannot write to widget-bound keys (sel_unit_row/col) after widgets
            # are rendered. Write to unbound proxy keys; the sidebar consumes them
            # BEFORE the selectbox widgets are instantiated on the next rerun.
            sel = event.selection if (event and hasattr(event, 'selection')) else {}
            point_indices = sel.get('point_indices', [])
            if point_indices:
                clicked_row = panel_df.iloc[point_indices[0]]
                ux = clicked_row.get('UNIT_INDEX_X')
                uy = clicked_row.get('UNIT_INDEX_Y')

                if ux is not None and uy is not None:
                    # Write to PROXY keys, NOT the widget-bound keys
                    st.session_state['_pending_row'] = int(uy)
                    st.session_state['_pending_col'] = int(ux)
                    st.session_state['_pending_nav'] = True
                    # _pending_view is consumed BEFORE the radio renders on next rerun
                    st.session_state['_pending_view'] = "🔬 Single Unit Inspection"

                    # Pre-compute and store the physical offset
                    ox, oy = calculate_physical_unit_origin(
                        int(uy), int(ux),
                        panel_rows_per_quad=st.session_state.get('quad_rows_input', 6),
                        panel_cols_per_quad=st.session_state.get('quad_cols_input', 6),
                        dyn_gap_x=st.session_state.get('dyn_gap_x_input', 5.0),
                        dyn_gap_y=st.session_state.get('dyn_gap_y_input', 3.5),
                    )
                    st.session_state['manual_offset_x'] = float(round(ox, 3))
                    st.session_state['manual_offset_y'] = float(round(oy, 3))
                    st.rerun()

        else:
            st.info("Upload AOI defect data to view the Panel Defect Map.")

    elif view_mode == "🔬 Single Unit Inspection":
        # Build overlay config from sidebar controls
        config = OverlayConfig()
        config.offset_x = align_args.get('manual_offset_x', 0.0)
        config.offset_y = align_args.get('manual_offset_y', 0.0)

        # VRS filter — reuse the same filter config the visualizer already applies
        # instead of duplicating the logic here. We pass it through OverlayConfig
        # and let _add_defect_traces handle grouping internally.
        vrs_df = defect_df.copy()
        if not vrs_df.empty:
            dtype_sel = st.session_state.get('defect_type_select')
            bu_sel    = st.session_state.get('buildup_filter_select')
            side_sel  = st.session_state.get('side_filter_select', 'Both')
            if dtype_sel and 'DEFECT_TYPE' in vrs_df.columns:
                vrs_df = vrs_df[vrs_df['DEFECT_TYPE'].isin(dtype_sel)]
            if bu_sel and 'BUILDUP' in vrs_df.columns:
                vrs_df = vrs_df[vrs_df['BUILDUP'].isin(bu_sel)]
            if side_sel != 'Both' and 'SIDE' in vrs_df.columns:
                vrs_df = vrs_df[vrs_df['SIDE'] == ('F' if side_sel == 'Front' else 'B')]
        
        # VRS Defect Stepper Console (with priority scoring)
        vrs_col1, vrs_col2, vrs_col3, vrs_col4, vrs_col5 = st.columns([3, 1.5, 1.5, 3, 2])
        with vrs_col1:
            vrs_mode = st.toggle("🎯 VRS Auto-Zoom Mode", value=False, help="Enable Defect Review Station mode to auto-pan cameras")

        num_def = len(vrs_df)
        if vrs_mode and num_def > 0:
            # Sort defects by priority score (highest first)
            from scoring import score_defect_priority
            vrs_df = vrs_df.copy()
            vrs_df['_PRIORITY'] = score_defect_priority(vrs_df)
            vrs_df = vrs_df.sort_values('_PRIORITY', ascending=False).reset_index(drop=True)

            if 'vrs_idx' not in st.session_state:
                st.session_state['vrs_idx'] = 0

            def prev_def(): st.session_state['vrs_idx'] = max(0, st.session_state['vrs_idx'] - 1)
            def next_def(): st.session_state['vrs_idx'] = min(num_def - 1, st.session_state['vrs_idx'] + 1)
            def jump_top(): st.session_state['vrs_idx'] = 0

            # Safeguard boundary if filtering reduced the total defects
            st.session_state['vrs_idx'] = min(max(0, st.session_state['vrs_idx']), num_def - 1)
            idx = st.session_state['vrs_idx']

            vrs_col2.button("⏪ Prev", on_click=prev_def, use_container_width=True)
            vrs_col3.button("Next ⏩", on_click=next_def, use_container_width=True)

            # Show defect info with priority score
            active_def = vrs_df.iloc[idx]
            priority = active_def.get('_PRIORITY', 0)
            dtype = active_def.get('DEFECT_TYPE', '?')
            vrs_col4.markdown(f"**{idx + 1}/{num_def}** — {dtype} (score: {priority:.0f})")
            vrs_col5.button("🔝 Jump to top", on_click=jump_top, use_container_width=True)

            # Extract active defect coordinates
            ax = active_def['ALIGNED_X']
            ay = active_def['ALIGNED_Y']

            config.active_defect_x = ax
            config.active_defect_y = ay
        elif vrs_mode:
            vrs_col4.markdown("**No defects visible**")

        st.divider()

        # Layer visibility (from checkboxes)
        if parsed and parsed.layers:
            config.visible_layers = [
                name for name in parsed.layers
                if st.session_state.get(f"vis_{name}", True)
            ]
            config.layer_opacities = {
                name: st.session_state.get(f"opacity_{name}", 0.5)
                for name in parsed.layers
            }

        # Defect filters
        if aoi and aoi.has_data:
            config.side_filter = st.session_state.get('side_filter_select', 'Both')
            config.buildup_filter = st.session_state.get('buildup_filter_select', aoi.buildup_numbers)
            config.defect_types = st.session_state.get('defect_type_select', aoi.defect_types)
            config.marker_style = st.session_state.get('marker_style_select', 'dot')
            config.color_mode = st.session_state.get('color_mode_select', 'by_type')

        # Determine board bounds for axis range
        if vrs_mode and num_def > 0 and config.active_defect_x is not None:
            # Override bounding box to zoom heavily onto the active defect
            zoom_radius = 2.0  # mm (camera perfectly centered on the defect)
            config.board_bounds = (
                config.active_defect_x - zoom_radius,
                config.active_defect_y - zoom_radius,
                config.active_defect_x + zoom_radius,
                config.active_defect_y + zoom_radius
            )
            # Frustum Culling Geometry Box (Matches camera + 1mm bleed buffer)
            config.crop_bounds = (
                config.active_defect_x - 3.0,
                config.active_defect_y - 3.0,
                config.active_defect_x + 3.0,
                config.active_defect_y + 3.0
            )
        elif parsed and parsed.layers:
            # Shift the bounding box camera so it follows the physically offset board
            ox = config.offset_x
            oy = config.offset_y
            
            # If bounded directly to a Single Unit, aggressively cull all neighboring Panel trace arrays!
            if align_args.get('unit_row') is not None and align_args.get('unit_col') is not None:
                config.crop_bounds = (ox - 2.0, oy - 2.0, ox + 50.0, oy + 50.0)
                config.board_bounds = config.crop_bounds
            else:
                bb = parsed.board_bounds
                config.board_bounds = (bb[0] + ox, bb[1] + oy, bb[2] + ox, bb[3] + oy)
                
        elif aoi and aoi.has_data:
            config.board_bounds = aoi.coord_bounds

        # Build and render figure
        gerber_layers = parsed.layers if parsed else {}

        if gerber_layers:
            fig = build_overlay_figure(gerber_layers, defect_df, config)
        elif not defect_df.empty:
            fig = build_defect_only_figure(defect_df, config)
        else:
            fig = None

        if fig:
            st.plotly_chart(
                fig,
                width='stretch',
                config={
                    'scrollZoom': True,
                    'displayModeBar': True,
                    'modeBarButtonsToAdd': ['drawrect', 'eraseshape'],
                    'displaylogo': False,
                },
            )

            # ── Export Pipeline ──────────────────────────────────────────────
            from export import export_current_view, export_unit_csv
            exp_col1, exp_col2, exp_col3 = st.columns([2, 2, 6])
            with exp_col1:
                try:
                    img_bytes = export_current_view(fig, fmt='png', scale=3)
                    st.download_button(
                        "📷 Export PNG",
                        data=img_bytes,
                        file_name="defect_overlay.png",
                        mime="image/png",
                        use_container_width=True,
                    )
                except Exception:
                    st.button("📷 Export PNG (kaleido required)", disabled=True, use_container_width=True)
            with exp_col2:
                csv_str = export_unit_csv(defect_df)
                st.download_button(
                    "📊 Export CSV",
                    data=csv_str,
                    file_name="defect_summary.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

    # ── SVG Unit View ─────────────────────────────────────────────────────────
    elif view_mode == "🧪 SVG Unit View":
        svg_store = st.session_state.get('svg_store', {})
        if not svg_store:
            st.info("Upload SVG files in the '🧪 SVG Layers (Experimental)' sidebar section first.")
        else:
            bu_nums, sides_avail = parse_svg_keys(svg_store)
            cell_w = float(st.session_state.get('svg_cell_w', 35.0))
            cell_h = float(st.session_state.get('svg_cell_h', 39.0))

            # BU capsule selector
            if 'svg_sel_bu' not in st.session_state:
                st.session_state['svg_sel_bu'] = bu_nums[0] if bu_nums else 1
            bu_c = st.columns(len(bu_nums), gap="small")
            for _i, _bn in enumerate(bu_nums):
                _act = st.session_state['svg_sel_bu'] == _bn
                def _sel_bu(_b=_bn): st.session_state['svg_sel_bu'] = _b
                bu_c[_i].button(f"BU-{_bn:02d}", key=f"svg_bu_{_bn}",
                                type="primary" if _act else "secondary",
                                width="stretch", on_click=_sel_bu)

            # Side capsule selector
            if 'svg_sel_side' not in st.session_state:
                st.session_state['svg_sel_side'] = sides_avail[0] if sides_avail else 'F'
            s_cols = st.columns(len(sides_avail), gap="small")
            for _i, _s in enumerate(sides_avail):
                _act = st.session_state['svg_sel_side'] == _s
                def _sel_side(_s2=_s): st.session_state['svg_sel_side'] = _s2
                _lbl = "Front" if _s == "F" else "Back"
                s_cols[_i].button(_lbl, key=f"svg_side_{_s}",
                                  type="primary" if _act else "secondary",
                                  width="stretch", on_click=_sel_side)

            sel_bu = st.session_state['svg_sel_bu']
            sel_side = st.session_state['svg_sel_side']
            svg_key = f"BU-{sel_bu:02d}_{sel_side}"
            svg_str = svg_store.get(svg_key)

            if not svg_str:
                st.warning(f"SVG not found for {svg_key}.svg — check uploaded files.")
            else:
                st.divider()
                # Unit position (reuse sidebar selectors)
                row_val = st.session_state.get('sel_unit_row', 'All')
                col_val = st.session_state.get('sel_unit_col', 'All')
                if row_val == 'All' or col_val == 'All':
                    st.info("Select a specific Unit Row and Unit Col in the sidebar to position the SVG overlay.")
                    unit_x, unit_y = 0.0, 0.0
                else:
                    unit_x, unit_y = calculate_physical_unit_origin(
                        int(row_val), int(col_val),
                        panel_rows_per_quad=st.session_state.get('quad_rows_input', 6),
                        panel_cols_per_quad=st.session_state.get('quad_cols_input', 6),
                        dyn_gap_x=st.session_state.get('dyn_gap_x_input', 5.0),
                        dyn_gap_y=st.session_state.get('dyn_gap_y_input', 3.5),
                    )

                data_url = svg_to_data_url(svg_str)

                fig_svg = go.Figure()
                # SVG as background (Y-up: top-left corner = x, y+height)
                fig_svg.add_layout_image(
                    source=data_url,
                    xref="x", yref="y",
                    x=unit_x, y=unit_y + cell_h,
                    sizex=cell_w, sizey=cell_h,
                    sizing="stretch",
                    layer="below",
                    opacity=1.0,
                )

                # Overlay AOI defect dots for this unit
                if not defect_df.empty and 'ALIGNED_X' in defect_df.columns:
                    unit_df = defect_df.copy()
                    if row_val != 'All':
                        unit_df = unit_df[unit_df['UNIT_INDEX_Y'] == int(row_val)] if 'UNIT_INDEX_Y' in unit_df.columns else unit_df
                    if col_val != 'All':
                        unit_df = unit_df[unit_df['UNIT_INDEX_X'] == int(col_val)] if 'UNIT_INDEX_X' in unit_df.columns else unit_df
                    if not unit_df.empty:
                        fig_svg.add_trace(go.Scatter(
                            x=unit_df['ALIGNED_X'], y=unit_df['ALIGNED_Y'],
                            mode='markers',
                            marker=dict(size=8, color='red', symbol='circle-open', line=dict(width=2)),
                            name='Defects',
                            hovertext=unit_df.get('DEFECT_TYPE', pd.Series()),
                        ))

                fig_svg.update_layout(
                    xaxis=dict(range=[unit_x - 1, unit_x + cell_w + 1], scaleanchor="y", scaleratio=1, title="X (mm)"),
                    yaxis=dict(range=[unit_y - 1, unit_y + cell_h + 1], title="Y (mm)"),
                    margin=dict(l=0, r=0, t=30, b=0),
                    height=600,
                    title=f"SVG Unit View — {svg_key} | Row {row_val}, Col {col_val}",
                    plot_bgcolor='#1a1a2e',
                    paper_bgcolor='#1a1a2e',
                    font_color='white',
                )
                st.plotly_chart(fig_svg, width='stretch', config={'scrollZoom': True, 'displaylogo': False})

    # ── Calibration Wizard ──────────────────────────────────────────────────
    elif view_mode == "🎯 Calibration Wizard":
        st.subheader("Fiducial Auto-Calibration Wizard")
        st.markdown("""
        **Instructions**: Click 3 fiducial points on the ODB++ render below, enter their
        corresponding AOI machine coordinates, and the system computes the full affine
        transform automatically. Store the calibration per machine ID.
        """)

        if parsed and parsed.layers:
            from alignment import _align_affine

            # Render ODB++ outline + fiducials for clicking
            calib_fig = go.Figure()
            outline_layer = next((l for l in parsed.layers.values() if l.layer_type == 'outline'), None)
            if outline_layer:
                from visualizer import _geometry_to_coords
                for poly in outline_layer.polygons:
                    px, py = _geometry_to_coords(poly)
                    calib_fig.add_trace(go.Scatter(x=px, y=py, mode='lines',
                        line=dict(color='gold', width=2), name='Board Outline', showlegend=False))

            # Show known ODB++ fiducials
            if parsed.fiducials:
                fid_x = [f[0] for f in parsed.fiducials]
                fid_y = [f[1] for f in parsed.fiducials]
                calib_fig.add_trace(go.Scatter(
                    x=fid_x, y=fid_y, mode='markers+text',
                    marker=dict(size=15, color='cyan', symbol='diamond'),
                    text=[f"F{i+1}" for i in range(len(parsed.fiducials))],
                    textposition='top center',
                    name='ODB++ Fiducials',
                ))

            bb = parsed.board_bounds
            calib_fig.update_layout(
                plot_bgcolor='#1a1a2e', paper_bgcolor='#16213e',
                font=dict(color='#e0e0e0'),
                xaxis=dict(title='X (mm)', range=[bb[0]-5, bb[2]+5], scaleanchor='y'),
                yaxis=dict(title='Y (mm)', range=[bb[1]-5, bb[3]+5]),
                height=500,
            )
            st.plotly_chart(calib_fig, use_container_width=True)

            # Manual fiducial entry
            st.subheader("Enter AOI Fiducial Coordinates")
            n_fids = len(parsed.fiducials) if parsed.fiducials else 3
            machine_id = st.text_input("Machine ID", value="AOI-01", key="calib_machine_id")

            aoi_fid_entries = []
            cols = st.columns(min(n_fids, 4))
            for i in range(min(n_fids, 4)):
                with cols[i]:
                    st.caption(f"**Fiducial {i+1}**")
                    if parsed.fiducials and i < len(parsed.fiducials):
                        st.text(f"ODB++: ({parsed.fiducials[i][0]:.2f}, {parsed.fiducials[i][1]:.2f})")
                    ax = st.number_input(f"AOI X{i+1} (mm)", value=0.0, key=f"calib_ax_{i}", format="%.3f")
                    ay = st.number_input(f"AOI Y{i+1} (mm)", value=0.0, key=f"calib_ay_{i}", format="%.3f")
                    aoi_fid_entries.append((ax, ay))

            if st.button("Compute Calibration", type="primary"):
                if parsed.fiducials and len(aoi_fid_entries) >= 2:
                    n_use = min(len(parsed.fiducials), len(aoi_fid_entries))
                    gerber_fids = parsed.fiducials[:n_use]
                    aoi_fids = aoi_fid_entries[:n_use]

                    # Check that AOI points are not all zeros
                    if all(abs(x) < 1e-6 and abs(y) < 1e-6 for x, y in aoi_fids):
                        st.error("All AOI coordinates are zero. Enter the machine-reported fiducial positions.")
                    else:
                        calib_result = _align_affine(gerber_fids, aoi_fids,
                                                     parsed.board_bounds, parsed.board_bounds)
                        st.success(f"Calibration computed: rotation={calib_result.rotation_deg:.4f}°, "
                                   f"scale={calib_result.scale_x:.6f}")
                        if calib_result.warnings:
                            for w in calib_result.warnings:
                                st.warning(w)

                        # Store calibration per machine
                        if 'calibrations' not in st.session_state:
                            st.session_state['calibrations'] = {}
                        st.session_state['calibrations'][machine_id] = {
                            'matrix': calib_result.transform_matrix.tolist(),
                            'rotation_deg': calib_result.rotation_deg,
                            'scale_x': calib_result.scale_x,
                            'scale_y': calib_result.scale_y,
                        }
                        st.info(f"Calibration stored for machine '{machine_id}'. "
                                "It will be used automatically for subsequent panels on this machine.")
                else:
                    st.error("Need at least 2 ODB++ fiducials and 2 AOI fiducial entries.")

            # Show stored calibrations
            cals = st.session_state.get('calibrations', {})
            if cals:
                st.subheader("Stored Calibrations")
                for mid, cal in cals.items():
                    st.text(f"Machine '{mid}': rot={cal['rotation_deg']:.4f}°, "
                            f"sx={cal['scale_x']:.6f}, sy={cal['scale_y']:.6f}")
        else:
            st.info("Upload an ODB++ archive to use the Calibration Wizard.")

    # ── SVG Panel View ────────────────────────────────────────────────────────
    elif view_mode == "🗺️ SVG Panel View":
        svg_store = st.session_state.get('svg_store', {})
        if not svg_store:
            st.info("Upload SVG files in the '🧪 SVG Layers (Experimental)' sidebar section first.")
        else:
            bu_nums, sides_avail = parse_svg_keys(svg_store)
            cell_w = float(st.session_state.get('svg_cell_w', 35.0))
            cell_h = float(st.session_state.get('svg_cell_h', 39.0))
            q_rows = int(st.session_state.get('quad_rows_input', 6))
            q_cols = int(st.session_state.get('quad_cols_input', 6))
            d_gap_x = float(st.session_state.get('dyn_gap_x_input', 5.0))
            d_gap_y = float(st.session_state.get('dyn_gap_y_input', 3.5))

            # BU / Side selectors (reuse svg_sel_bu / svg_sel_side from SVG Unit View)
            if 'svg_sel_bu' not in st.session_state:
                st.session_state['svg_sel_bu'] = bu_nums[0] if bu_nums else 1
            if 'svg_sel_side' not in st.session_state:
                st.session_state['svg_sel_side'] = sides_avail[0] if sides_avail else 'F'

            pbu_c = st.columns(len(bu_nums), gap="small")
            for _i, _bn in enumerate(bu_nums):
                _act = st.session_state['svg_sel_bu'] == _bn
                def _psel_bu(_b=_bn): st.session_state['svg_sel_bu'] = _b
                pbu_c[_i].button(f"BU-{_bn:02d}", key=f"pansvg_bu_{_bn}",
                                 type="primary" if _act else "secondary",
                                 width="stretch", on_click=_psel_bu)
            ps_cols = st.columns(len(sides_avail), gap="small")
            for _i, _s in enumerate(sides_avail):
                _act = st.session_state['svg_sel_side'] == _s
                def _psel_side(_s2=_s): st.session_state['svg_sel_side'] = _s2
                _lbl = "Front" if _s == "F" else "Back"
                ps_cols[_i].button(_lbl, key=f"pansvg_side_{_s}",
                                   type="primary" if _act else "secondary",
                                   width="stretch", on_click=_psel_side)

            sel_bu = st.session_state['svg_sel_bu']
            sel_side = st.session_state['svg_sel_side']
            svg_key = f"BU-{sel_bu:02d}_{sel_side}"
            svg_str = svg_store.get(svg_key)

            if not svg_str:
                st.warning(f"SVG not found for {svg_key}.svg — check uploaded files.")
            else:
                st.divider()
                total_units = q_rows * q_cols * 4  # 2×2 quads
                if total_units > 64:
                    st.warning(
                        f"⚠️ {total_units} units to tile — Plotly may be slow above ~64 images. "
                        "Consider reducing rows/cols per quadrant in the sidebar for a preview."
                    )

                ctx = calculate_geometry(q_rows, q_cols, d_gap_x, d_gap_y)
                data_url = svg_to_data_url(svg_str)

                fig_pan = go.Figure()

                # 1. Copper frame (rounded rect, ported from faster-aoi shapes.py)
                frame_path = get_rounded_rect_path(0, 0, FRAME_WIDTH, FRAME_HEIGHT, r=20.0)
                fig_pan.add_shape(
                    type="path", path=frame_path,
                    fillcolor="#C87533", line=dict(color="#8B4513", width=3),
                    layer="below"
                )

                # 2. Tile SVG for every unit in all 4 quadrants
                images = []
                for _q_label, (q_ox, q_oy) in ctx.quadrant_origins.items():
                    for _r in range(q_rows):
                        for _c in range(q_cols):
                            ux = q_ox + INTER_UNIT_GAP + _c * ctx.stride_x
                            uy = q_oy + INTER_UNIT_GAP + _r * ctx.stride_y
                            images.append(dict(
                                source=data_url,
                                xref="x", yref="y",
                                x=ux, y=uy + ctx.cell_height,
                                sizex=ctx.cell_width, sizey=ctx.cell_height,
                                sizing="stretch", layer="below", opacity=1.0,
                            ))
                fig_pan.update_layout(images=images)

                # 3. Overlay ALL defect dots
                if not defect_df.empty and 'ALIGNED_X' in defect_df.columns:
                    # Filter to selected BU/side if columns available
                    pan_df = defect_df.copy()
                    if 'BUILDUP' in pan_df.columns:
                        pan_df = pan_df[pan_df['BUILDUP'] == sel_bu]
                    if 'SIDE' in pan_df.columns:
                        side_full = "Front" if sel_side == "F" else "Back"
                        pan_df = pan_df[pan_df['SIDE'] == side_full]
                    if not pan_df.empty:
                        # Ensure safety for missing unit indices
                        if 'UNIT_INDEX_X' not in pan_df.columns:
                            pan_df['UNIT_INDEX_X'] = 0.0
                        if 'UNIT_INDEX_Y' not in pan_df.columns:
                            pan_df['UNIT_INDEX_Y'] = 0.0

                        custom_data = pan_df[['UNIT_INDEX_Y', 'UNIT_INDEX_X']].values

                        fig_pan.add_trace(go.Scatter(
                            x=pan_df['ALIGNED_X'], y=pan_df['ALIGNED_Y'],
                            mode='markers',
                            marker=dict(size=5, color='red', symbol='circle-open', line=dict(width=1.5)),
                            name='Defects',
                            customdata=custom_data,
                            hovertext=pan_df.get('DEFECT_TYPE', pd.Series()),
                        ))

                fig_pan.update_layout(
                    xaxis=dict(range=[-10, FRAME_WIDTH + 10], scaleanchor="y", scaleratio=1, title="X (mm)"),
                    yaxis=dict(range=[-10, FRAME_HEIGHT + 10], title="Y (mm)"),
                    margin=dict(l=0, r=0, t=30, b=0),
                    height=750,
                    title=f"SVG Panel View — {svg_key} | {q_rows}×{q_cols} per quad | {total_units} units",
                    plot_bgcolor='#1a1a2e',
                    paper_bgcolor='#1a1a2e',
                    font_color='white',
                    clickmode='event+select',
                )

                # Capture clicks on defects to jump to SVG Unit View
                selection = st.plotly_chart(
                    fig_pan,
                    width='stretch',
                    config={'scrollZoom': True, 'displaylogo': False},
                    on_select="rerun",
                    selection_mode="points",
                    key="svg_panel_overview_chart",
                )

                if selection and "selection" in selection:
                    pts = selection["selection"].get("points", [])
                    if pts:
                        cd = pts[0].get("customdata", [])
                        if len(cd) >= 2:
                            uy, ux = cd[0], cd[1]
                            st.session_state['_pending_row'] = int(uy)
                            st.session_state['_pending_col'] = int(ux)
                            st.session_state['_pending_nav'] = True
                            st.session_state['_pending_view'] = "🧪 SVG Unit View"
                            st.rerun()



    # ---- Alignment & Coordinate Debug Panel ----
    with st.expander("🔧 Alignment & Coordinate Debug", expanded=False):
        if alignment:
            debug = get_debug_info(alignment)

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Method", debug['method'].title())
            col2.metric("Overlap", f"{debug['overlap_pct']:.1f}%")
            col3.metric("Offset X", f"{debug['offset_x_mm']:.3f} mm")
            col4.metric("Offset Y", f"{debug['offset_y_mm']:.3f} mm")

            # Confidence and fiducial metrics
            conf = debug.get('confidence', 0.0)
            fids_used = debug.get('fiducials_used', 0)
            mc1, mc2 = st.columns(2)
            conf_color = "normal" if conf >= 0.5 else "off"
            mc1.metric("Confidence", f"{conf:.0%}", delta=f"{fids_used} fiducials" if fids_used > 0 else "no fiducials")
            if alignment.method == 'affine' and conf >= 0.5:
                mc2.success("Fiducial auto-alignment active — manual offsets overridden")
            elif alignment.method == 'offset':
                mc2.info("Bounding-box offset alignment — provide fiducials for better accuracy")

            if debug['rotation_deg'] != 0 or debug['scale_x'] != 1.0:
                col5, col6 = st.columns(2)
                col5.metric("Rotation", f"{debug['rotation_deg']:.4f}°")
                col6.metric("Scale", f"{debug['scale_x']:.6f}")

            # Warnings
            for w in debug['warnings']:
                st.warning(w, icon="⚠️")

            # Bounds comparison
            st.subheader("Coordinate Extents")
            bounds_col1, bounds_col2, bounds_col3 = st.columns(3)

            with bounds_col1:
                st.caption("**ODB++ Bounds (mm)**")
                gb = debug['gerber_bounds']
                st.text(
                    f"X: {gb['min_x']:.3f} → {gb['max_x']:.3f}\n"
                    f"Y: {gb['min_y']:.3f} → {gb['max_y']:.3f}"
                )

            with bounds_col2:
                st.caption("**AOI Bounds (Original mm)**")
                ab = debug['aoi_bounds']
                st.text(
                    f"X: {ab['min_x']:.3f} → {ab['max_x']:.3f}\n"
                    f"Y: {ab['min_y']:.3f} → {ab['max_y']:.3f}"
                )

            with bounds_col3:
                st.caption("**AOI Bounds (Aligned mm)**")
                if not defect_df.empty:
                    ax = defect_df['ALIGNED_X']
                    ay = defect_df['ALIGNED_Y']
                    st.text(f"X: {ax.min():.3f} → {ax.max():.3f}\nY: {ay.min():.3f} → {ay.max():.3f}")

            # Overlap Warning
            overlap = debug['overlap_pct']
            if overlap > 0:
                st.success(f"Overlapping Regions: YES ({overlap:.1f}%)")
            else:
                st.error("Overlapping Regions: NO")

            # ODB++ step/units info
            if parsed:
                st.caption(f"**Step**: `{parsed.step_name}` | **Units**: `{parsed.units}` | **Origin**: `x={parsed.origin_x}, y={parsed.origin_y}`")
                if parsed.unknown_symbols:
                    st.caption(f"**Unknown Symbols**: {', '.join(parsed.unknown_symbols)}")

            # Full JSON debug
            with st.expander("Raw Debug Data"):
                st.json(debug)

        elif aoi and aoi.has_data:
            st.info("No ODB++ data loaded — showing raw AOI coordinates without alignment")
            bounds = aoi.coord_bounds
            st.text(
                f"AOI X: {bounds[0]:.3f} → {bounds[2]:.3f} mm\n"
                f"AOI Y: {bounds[1]:.3f} → {bounds[3]:.3f} mm"
            )

    # ---- Defect Summary Panel ----
    if aoi and aoi.has_data:
        with st.expander("📊 Defect Summary", expanded=False):
            summary = (
                aoi.all_defects
                .groupby(['BUILDUP', 'SIDE', 'DEFECT_TYPE'], observed=True)
                .size()
                .reset_index(name='COUNT')
                .sort_values(['BUILDUP', 'SIDE', 'COUNT'], ascending=[True, True, False])
            )
            st.dataframe(
                summary,
                use_container_width=True,
                hide_index=True,
            )

            # Quick stats
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Defects", len(aoi.all_defects))
            col2.metric("Defect Types", len(aoi.defect_types))
            col3.metric("Buildup Layers", len(aoi.buildup_numbers))

    # ---- Cluster Triage Panel ----
    cluster_summary = st.session_state.get('_cluster_summary')
    if cluster_summary is not None and not cluster_summary.empty:
        with st.expander("🔬 Defect Cluster Triage", expanded=False):
            st.caption("Clusters ranked by defect count. Click 'Inspect' to navigate to VRS stepper for that cluster.")
            for i, crow in cluster_summary.iterrows():
                cid = crow['cluster_id']
                cnt = crow['defect_count']
                dtype = crow['dominant_type']
                pct = crow['dominant_pct']
                bu = crow['buildup_info']
                cx, cy = crow['centroid_x'], crow['centroid_y']
                st.markdown(
                    f"**Cluster {cid}**: {cnt} defects at ({cx}, {cy}) — "
                    f"{pct:.0f}% {dtype}" + (f" — {bu}" if bu else "")
                )

    # ---- Job Registration & Trend Analysis ----
    if aoi and aoi.has_data:
        with st.expander("📈 Job Registry & Trend Analysis", expanded=False):
            from job_registry import register_job, list_jobs, get_job_density_summary
            import hashlib as _hl

            reg_col1, reg_col2, reg_col3, reg_col4 = st.columns([3, 3, 2, 2])
            job_id = reg_col1.text_input("Job ID", value="", key="reg_job_id", placeholder="e.g. LOT-2026-0327")
            panel_id = reg_col2.text_input("Panel ID", value="", key="reg_panel_id", placeholder="e.g. Panel-01")
            date_val = reg_col3.date_input("Date", key="reg_date")
            if reg_col4.button("Register Job", use_container_width=True) and job_id:
                _hash = _hl.md5(aoi.all_defects.to_csv().encode()).hexdigest()
                ok = register_job(job_id, panel_id, str(date_val), _hash, aoi.all_defects)
                if ok:
                    st.success(f"Registered job {job_id}/{panel_id}")
                else:
                    st.info(f"Job {job_id}/{panel_id} already registered")

            st.divider()
            st.subheader("Trend Analysis")
            jobs_df = list_jobs()
            if jobs_df.empty:
                st.info("No jobs registered yet. Register the current inspection above to start trending.")
            else:
                st.dataframe(jobs_df[['job_id', 'panel_id', 'date']].drop_duplicates(),
                             use_container_width=True, hide_index=True)

                density = get_job_density_summary()
                if not density.empty:
                    # Aggregate: total defects per job
                    job_totals = density.groupby(['job_id', 'date'])['total_defects'].sum().reset_index()
                    import plotly.express as px
                    trend_fig = px.bar(
                        job_totals, x='date', y='total_defects', color='job_id',
                        title="Defect Count by Job",
                        labels={'total_defects': 'Total Defects', 'date': 'Date'},
                    )
                    trend_fig.update_layout(
                        plot_bgcolor='#1a1a2e', paper_bgcolor='#16213e',
                        font=dict(color='#e0e0e0'),
                    )
                    st.plotly_chart(trend_fig, use_container_width=True)

                    # Heatmap: defect density per unit position
                    unit_density = density.groupby(['unit_row', 'unit_col'])['total_defects'].sum().reset_index()
                    if not unit_density.empty:
                        heatmap_fig = px.density_heatmap(
                            unit_density, x='unit_col', y='unit_row', z='total_defects',
                            title="Defect Density Heatmap (All Jobs)",
                            labels={'unit_col': 'Unit Column', 'unit_row': 'Unit Row'},
                        )
                        heatmap_fig.update_layout(
                            plot_bgcolor='#1a1a2e', paper_bgcolor='#16213e',
                            font=dict(color='#e0e0e0'),
                        )
                        st.plotly_chart(heatmap_fig, use_container_width=True)

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
