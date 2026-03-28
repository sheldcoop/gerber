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
from gerber_renderer import render_odb_to_cam, RenderedODB, PanelLayout, save_render_cache, load_render_cache
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
from visualizer import build_overlay_figure, build_defect_only_figure, OverlayConfig, _apply_layout
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
        'rendered_odb': None,        # RenderedODB (Gerbonara CAM SVGs)
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

            # Render CAM-quality SVGs via Gerbonara (with disk cache)
            if gerber_file:
                try:
                    gerber_file.seek(0)
                    _tgz_bytes = gerber_file.read()
                    gerber_file.seek(0)

                    rendered = load_render_cache(_tgz_bytes)
                    _from_cache = rendered is not None

                    if not rendered:
                        with st.spinner("Rendering CAM-quality copper layers..."):
                            rendered = render_odb_to_cam(_tgz_bytes, gerber_file.name)
                            save_render_cache(_tgz_bytes, rendered)

                    st.session_state['rendered_odb'] = rendered

                    # Auto-populate panel grid from TGZ step-repeat data
                    if rendered.panel_layout:
                        _pl = rendered.panel_layout
                        _qr = max(1, _pl.rows // 2)
                        _qc = max(1, _pl.cols // 2)
                        st.session_state['quad_rows_input'] = _qr
                        st.session_state['quad_cols_input'] = _qc
                        st.info(
                            f"Panel layout from TGZ: {_pl.total_units} units "
                            f"({_pl.cols}×{_pl.rows} grid, "
                            f"unit size: {_pl.unit_bounds[0]:.1f}×{_pl.unit_bounds[1]:.1f} mm)"
                        )

                    if rendered.layers:
                        layer_names = list(rendered.layers.keys())
                        st.session_state['cam_layer_select'] = layer_names[0]
                        total_features = sum(l.feature_count for l in rendered.layers.values())
                        if _from_cache:
                            st.success(
                                f"Loaded from design cache — {len(rendered.layers)} layers, "
                                f"{total_features:,} features"
                            )
                        else:
                            st.success(
                                f"CAM render: {len(rendered.layers)} copper layers, "
                                f"{total_features:,} features "
                                f"(bounds: {rendered.board_bounds[2]-rendered.board_bounds[0]:.1f} x "
                                f"{rendered.board_bounds[3]-rendered.board_bounds[1]:.1f} mm)"
                            )
                    for w in rendered.warnings:
                        st.warning(w, icon="⚠️")
                except Exception as e:
                    st.warning(f"CAM rendering failed (falling back to Shapely): {e}")

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

            # Grid derived from TGZ step-repeat; fallback constants used only when
            # AOI data lacks UNIT_INDEX_Y/X columns.
            quad_rows = int(st.session_state.get('quad_rows_input', 6))
            quad_cols = int(st.session_state.get('quad_cols_input', 6))

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
            with col4:
                unit_col = st.selectbox("Unit Col", unit_col_opts, key='sel_unit_col', on_change=update_offsets)
            
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
                ['by_type', 'by_verification', 'by_buildup', 'by_severity'],
                format_func=lambda x: {'by_type': 'By Defect Type', 'by_verification': 'By Verification',
                                       'by_buildup': 'By Buildup', 'by_severity': 'By Severity'}.get(x, x),
                key='color_mode_select',
            )

        # ---- Background Source ----
        _has_rendered = bool(st.session_state.get('rendered_odb'))
        _has_odb = bool(st.session_state.get('parsed_odb'))
        if _has_rendered or _has_odb:
            if 'bg_source' not in st.session_state:
                st.session_state['bg_source'] = 'CAM (Gerbonara)' if _has_rendered else 'ODB++ / Shapely'
            _bg_options = []
            if _has_rendered:
                _bg_options.append('CAM (Gerbonara)')
            if _has_odb:
                _bg_options.append('ODB++ / Shapely')
            if not _bg_options:
                _bg_options = ['ODB++ / Shapely']
            st.radio(
                "Background source",
                _bg_options,
                key='bg_source',
                horizontal=True,
                help="Select rendering backend for PCB layer background",
            )
        else:
            st.session_state['bg_source'] = 'ODB++ / Shapely'


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

    _tabs = ["🔭 Panel Overview", "🔬 Single Unit Inspection", "🗺️ Commonality", "🎯 Calibration Wizard"]
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
            panel_fig.update_layout(showlegend=False)

            # ── CAM (Gerbonara) background tiling (pre-cached panel PNG) ────
            _bg_source = st.session_state.get('bg_source', 'ODB++ / Shapely')
            _rendered_panel = st.session_state.get('rendered_odb')
            if _bg_source == 'CAM (Gerbonara)' and _rendered_panel and _rendered_panel.panel_layout:
                # Pick the first visible layer's pre-cached panel PNG
                _panel_png_url = None
                for _ln in _rendered_panel.layers:
                    if st.session_state.get(f"vis_{_ln}", False):
                        _panel_png_url = _rendered_panel.layers[_ln].panel_png_data_url
                        break
                if not _panel_png_url and _rendered_panel.layers:
                    _panel_png_url = next(iter(_rendered_panel.layers.values())).panel_png_data_url

                if _panel_png_url:
                    panel_fig.update_layout(images=[dict(
                        source=_panel_png_url,
                        xref="x", yref="y",
                        x=0, y=FRAME_HEIGHT,
                        sizex=FRAME_WIDTH, sizey=FRAME_HEIGHT,
                        sizing="stretch", layer="below", opacity=1.0,
                    )])

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
                                hoverinfo='name', showlegend=False,
                            ))
                    # Store for cluster panel below
                    st.session_state['_cluster_summary'] = cluster_summary
                    st.session_state['_clustered_df'] = clustered_df

            # ── Professional PCB substrate panel background ───────────────
            # Dark green solder-mask base for the whole frame
            frame_bx1, frame_by1, frame_bx2, frame_by2 = quad_bounds['frame']
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

            # Draw individual unit cells within each quadrant
            _pq_rows = int(st.session_state.get('quad_rows_input', 6))
            _pq_cols = int(st.session_state.get('quad_cols_input', 6))
            _pd_gap_x = float(st.session_state.get('dyn_gap_x_input', 5.0))
            _pd_gap_y = float(st.session_state.get('dyn_gap_y_input', 3.5))
            _pctx = calculate_geometry(_pq_rows, _pq_cols, _pd_gap_x, _pd_gap_y)
            for _, (q_ox, q_oy) in _pctx.quadrant_origins.items():
                for _pr in range(_pq_rows):
                    for _pc in range(_pq_cols):
                        _ux = q_ox + INTER_UNIT_GAP + _pc * _pctx.stride_x
                        _uy = q_oy + INTER_UNIT_GAP + _pr * _pctx.stride_y
                        panel_fig.add_shape(
                            type="rect",
                            x0=_ux, y0=_uy,
                            x1=_ux + _pctx.cell_width, y1=_uy + _pctx.cell_height,
                            fillcolor="rgba(0,180,100,0.07)",
                            line=dict(color="rgba(0,220,130,0.5)", width=0.8),
                            layer="below",
                        )

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

        elif st.session_state.get('rendered_odb') and st.session_state.get('bg_source') == 'CAM (Gerbonara)':
            # CAM-only mode: no AOI data, but TGZ is rendered — show tiled panel PNG
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
                # Use pre-cached panel PNG (instant layer switching)
                _panel_png = None
                for _ln2 in _rodb.layers:
                    if st.session_state.get(f"vis_{_ln2}", False):
                        _panel_png = _rodb.layers[_ln2].panel_png_data_url
                        break
                if not _panel_png:
                    _panel_png = next(iter(_rodb.layers.values())).panel_png_data_url

                if _panel_png:
                    _cam_fig.update_layout(images=[dict(
                        source=_panel_png,
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
        elif _rendered_odb_bounds := st.session_state.get('rendered_odb'):
            # Use CAM bounds (Gerbonara — more accurate than Shapely)
            ox = config.offset_x
            oy = config.offset_y
            bb = _rendered_odb_bounds.board_bounds
            config.board_bounds = (bb[0] + ox, bb[1] + oy, bb[2] + ox, bb[3] + oy)
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
        _bg_source = st.session_state.get('bg_source', 'ODB++ / Shapely')
        _use_cam_bg = (_bg_source == 'CAM (Gerbonara)')
        _rendered_odb = st.session_state.get('rendered_odb')

        # When using CAM background, don't render Shapely layers (avoid double-render)
        gerber_layers = parsed.layers if (parsed and not _use_cam_bg) else {}

        if gerber_layers:
            fig = build_overlay_figure(
                gerber_layers, defect_df, config,
                drill_hits=parsed.drill_hits if parsed else None,
                components=parsed.components if parsed else None,
            )
        elif not defect_df.empty:
            fig = build_defect_only_figure(defect_df, config)
        else:
            fig = go.Figure()
            _apply_layout(fig, config)

        # ── CAM (Gerbonara) SVG background ───────────────────────────────
        if _use_cam_bg and _rendered_odb and _rendered_odb.layers and fig is not None:
            # Use Layer Controls checkboxes to determine which CAM layers to show
            _cam_layers_to_show = [
                name for name in _rendered_odb.layers
                if st.session_state.get(f"vis_{name}", False)
            ]
            # Fallback: if no layers selected, show first available
            if not _cam_layers_to_show:
                _cam_layers_to_show = list(_rendered_odb.layers.keys())[:1]

            # Use pre-cached data URLs — zero re-rendering on toggle
            _is_multi = len(_cam_layers_to_show) > 1

            for _ci, _cam_ln in enumerate(_cam_layers_to_show):
                _cam_lyr = _rendered_odb.layers.get(_cam_ln)
                if not _cam_lyr:
                    continue

                # Instant: just pick the right pre-cached data URL
                if _is_multi and _cam_lyr.color_svg_urls:
                    _data_url = next(iter(_cam_lyr.color_svg_urls.values()))
                else:
                    _data_url = _cam_lyr.svg_data_url

                _cb = _cam_lyr.bounds
                ox = config.offset_x
                oy = config.offset_y

                fig.add_layout_image(dict(
                    source=_data_url,
                    xref="x", yref="y",
                    x=_cb[0] + ox,
                    y=_cb[3] + oy,
                    sizex=_cb[2] - _cb[0],
                    sizey=_cb[3] - _cb[1],
                    sizing="stretch",
                    layer="below",
                    opacity=0.95 if not _is_multi else 0.7,
                ))

            # Update board bounds from CAM data if not already set
            if not (vrs_mode and num_def > 0) and config.board_bounds == (0, 0, 0, 0):
                _rbb = _rendered_odb.board_bounds
                config.board_bounds = (_rbb[0] + ox, _rbb[1] + oy, _rbb[2] + ox, _rbb[3] + oy)
                _apply_layout(fig, config)

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

    # ── Commonality / Superposition View ────────────────────────────────────
    elif view_mode == "🗺️ Commonality":
        st.markdown("### 🗺️ Commonality — Defect Superposition")
        st.caption("Normalise each selected unit's defects into local coordinates and overlay on a single reference unit.")

        if not (aoi and aoi.has_data):
            st.info("Upload AOI defect data to use the Commonality view.")
        elif 'UNIT_INDEX_X' not in aoi.all_defects.columns or 'UNIT_INDEX_Y' not in aoi.all_defects.columns:
            st.warning("⚠️ UNIT_INDEX_X / UNIT_INDEX_Y columns not found. Commonality view requires unit index data.")
        else:
            _q_rows_cm  = int(st.session_state.get('quad_rows_input', 6))
            _q_cols_cm  = int(st.session_state.get('quad_cols_input', 6))
            _d_gap_x_cm = float(st.session_state.get('dyn_gap_x_input', 5.0))
            _d_gap_y_cm = float(st.session_state.get('dyn_gap_y_input', 3.5))

            # ── Build full unit grid from TGZ (all 144 units), fall back to AOI ─
            _rodb_cm_pl = st.session_state.get('rendered_odb')
            if _rodb_cm_pl and _rodb_cm_pl.panel_layout:
                _pl_cm  = _rodb_cm_pl.panel_layout
                _rp_cm  = getattr(_pl_cm, 'unit_positions_raw', None) or _pl_cm.unit_positions
                _uxs_cm = sorted(set(round(x, 2) for x, _ in _rp_cm))
                _uys_cm = sorted(set(round(y, 2) for _, y in _rp_cm))
                _all_cm_pairs = [(ri, ci)
                                 for ri in range(len(_uys_cm))
                                 for ci in range(len(_uxs_cm))]
                # Update quad size from TGZ (rows/cols per quadrant = half of total)
                _q_rows_cm = max(1, len(_uys_cm) // 2)
                _q_cols_cm = max(1, len(_uxs_cm) // 2)
            else:
                # Fallback: only units that appear in AOI data
                _aup = (
                    aoi.all_defects[['UNIT_INDEX_Y', 'UNIT_INDEX_X']]
                    .dropna()
                    .drop_duplicates()
                    .sort_values(['UNIT_INDEX_Y', 'UNIT_INDEX_X'])
                    .values.tolist()
                )
                _all_cm_pairs = [(int(r), int(c)) for r, c in _aup]
            _all_cm_labels = [f"({r},{c})" for r, c in _all_cm_pairs]

            # Quadrant assignment: Q2=lower-left(rows 0-5,cols 0-5), Q1=top-left(rows 6-11,cols 0-5)
            #                     Q3=lower-right(rows 0-5,cols 6-11), Q4=top-right(rows 6-11,cols 6-11)
            def _cm_quad(r, c):
                qr, qc = r // _q_rows_cm, c // _q_cols_cm
                return {(0,0):'Q2',(0,1):'Q3',(1,0):'Q1',(1,1):'Q4'}.get((qr, qc), 'Other')

            _q1_cm_lbl = [l for (r,c),l in zip(_all_cm_pairs,_all_cm_labels) if _cm_quad(r,c)=='Q1']
            _q2_cm_lbl = [l for (r,c),l in zip(_all_cm_pairs,_all_cm_labels) if _cm_quad(r,c)=='Q2']
            _q3_cm_lbl = [l for (r,c),l in zip(_all_cm_pairs,_all_cm_labels) if _cm_quad(r,c)=='Q3']
            _q4_cm_lbl = [l for (r,c),l in zip(_all_cm_pairs,_all_cm_labels) if _cm_quad(r,c)=='Q4']

            # ── Initialise multiselect state (default = ALL) ──────────────────
            if 'cm_multiselect' not in st.session_state:
                st.session_state['cm_multiselect'] = _all_cm_labels

            def _cm_set(labels):
                def cb():
                    st.session_state['cm_multiselect'] = [l for l in labels if l in _all_cm_labels]
                return cb

            # ── Quick-select buttons ──────────────────────────────────────────
            _qs_cm = st.columns(6, gap="small")
            _qs_cm[0].button("ALL",   key="cm_all",   on_click=_cm_set(_all_cm_labels), use_container_width=True, type="primary")
            _qs_cm[1].button("Q1",    key="cm_q1",    on_click=_cm_set(_q1_cm_lbl),     use_container_width=True)
            _qs_cm[2].button("Q2",    key="cm_q2",    on_click=_cm_set(_q2_cm_lbl),     use_container_width=True)
            _qs_cm[3].button("Q3",    key="cm_q3",    on_click=_cm_set(_q3_cm_lbl),     use_container_width=True)
            _qs_cm[4].button("Q4",    key="cm_q4",    on_click=_cm_set(_q4_cm_lbl),     use_container_width=True)
            _qs_cm[5].button("Clear", key="cm_clear", on_click=_cm_set([]),             use_container_width=True)

            # Sanitise stale labels (units may no longer exist in current data)
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
                    _first_lyr_cm = next(iter(_rodb_cm.layers.values()))
                    _cam_min_x  = _first_lyr_cm.bounds[0]
                    _cam_min_y  = _first_lyr_cm.bounds[1]
                    _cam_cell_w = _first_lyr_cm.bounds[2] - _first_lyr_cm.bounds[0]
                    _cam_cell_h = _first_lyr_cm.bounds[3] - _first_lyr_cm.bounds[1]

                    # Sort unique TGZ x/y values to build (row, col) → origin mapping.
                    # TGZ uses center-origin (panel center = 0,0).
                    # AOI uses lower-left origin (panel edge = 0,0).
                    # Conversion: aoi_x = tgz_x + panel_width/2
                    # Unit lower-left in AOI space = raw_tgz_x + cam_min_x + panel_width/2
                    _raw_pos   = (getattr(_rodb_cm.panel_layout, 'unit_positions_raw', None)
                                  or _rodb_cm.panel_layout.unit_positions)
                    _uniq_x_cm = sorted(set(round(x, 2) for x, _ in _raw_pos))
                    _uniq_y_cm = sorted(set(round(y, 2) for _, y in _raw_pos))
                    _pl_w = _rodb_cm.panel_layout.panel_width   # 510mm
                    _pl_h = _rodb_cm.panel_layout.panel_height  # 515mm
                    _cm_origins = {
                        (ri, ci): (_uniq_x_cm[ci] + _cam_min_x + _pl_w / 2,
                                   _uniq_y_cm[ri] + _cam_min_y + _pl_h / 2)
                        for ri in range(len(_uniq_y_cm))
                        for ci in range(len(_uniq_x_cm))
                    }
                else:
                    # Fallback: manual geometry (uses user-set gap / grid params)
                    _cm_origins = {
                        (r, c): calculate_physical_unit_origin(
                            r, c, _q_rows_cm, _q_cols_cm, _d_gap_x_cm, _d_gap_y_cm
                        )
                        for r, c in _cm_sel_units
                    }

                # ── Scope-filter AOI data ─────────────────────────────────────
                _cm_src = aoi.all_defects.copy()
                _bu_cm   = st.session_state.get('buildup_filter_select', aoi.buildup_numbers)
                _side_cm = st.session_state.get('scope_side_sel', ['Front', 'Back'])
                if _bu_cm and 'BUILDUP' in _cm_src.columns:
                    _cm_src = _cm_src[_cm_src['BUILDUP'].isin(_bu_cm)]
                if 'SIDE' in _cm_src.columns:
                    if 'Front' in _side_cm and 'Back' not in _side_cm:
                        _cm_src = _cm_src[_cm_src['SIDE'] == 'F']
                    elif 'Back' in _side_cm and 'Front' not in _side_cm:
                        _cm_src = _cm_src[_cm_src['SIDE'] == 'B']

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
                else:
                    # ── DEBUG: show coordinate diagnostics ────────────────────
                    with st.expander("🔬 Coord Debug (remove after fix)", expanded=True):
                        st.write("**cam_min_x/y:**", _cam_min_x, _cam_min_y)
                        st.write("**cam_cell_w/h:**", _cam_cell_w, _cam_cell_h)
                        st.write("**_raw_pos (first 3):**", _raw_pos[:3] if _raw_pos else "EMPTY")
                        st.write("**_uniq_x_cm (first 6):**", _uniq_x_cm[:6])
                        st.write("**_uniq_y_cm (first 6):**", _uniq_y_cm[:6])
                        _sample_origins = dict(list(_cm_origins.items())[:4])
                        st.write("**_cm_origins (first 4):**", _sample_origins)
                        if not _cm_src.empty:
                            st.write("**Sample X_MM / Y_MM (first 3):**",
                                     _cm_src[['X_MM','Y_MM','UNIT_INDEX_Y','UNIT_INDEX_X']].head(3).to_dict('records'))

                    # ── Coordinate normalisation (vectorized) ─────────────────
                    # Subtract each unit's effective origin (TGZ pos + cam_min)
                    # so all units fold into local coords [0…cell_w] × [0…cell_h],
                    # matching the CAM SVG which is also shifted to start at (0, 0).
                    _pairs_cm = list(zip(
                        _cm_src['UNIT_INDEX_Y'].astype(int),
                        _cm_src['UNIT_INDEX_X'].astype(int),
                    ))
                    _ox_arr = [_cm_origins.get(p, (0.0, 0.0))[0] for p in _pairs_cm]
                    _oy_arr = [_cm_origins.get(p, (0.0, 0.0))[1] for p in _pairs_cm]

                    _cm_plot = _cm_src.copy()
                    _cm_plot['ALIGNED_X'] = _cm_src['X_MM'].values - _ox_arr
                    _cm_plot['ALIGNED_Y'] = _cm_src['Y_MM'].values - _oy_arr

                    # ── Build figure ──────────────────────────────────────────
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

                    # ── Background: CAM (Gerbonara) ──────────────────────────
                    _bg_src_cm    = st.session_state.get('bg_source', 'ODB++ / Shapely')
                    _rendered_cm  = st.session_state.get('rendered_odb')

                    if _bg_src_cm == 'CAM (Gerbonara)' and _rendered_cm and _rendered_cm.layers:
                        # Pick checked layers (same as Single Unit view logic)
                        _cm_cam_layers = [
                            n for n in _rendered_cm.layers
                            if st.session_state.get(f"vis_{n}", False)
                        ]
                        if not _cm_cam_layers:
                            _cm_cam_layers = list(_rendered_cm.layers.keys())[:1]

                        _is_multi_cm = len(_cm_cam_layers) > 1
                        for _cm_cam_ln in _cm_cam_layers:
                            _cm_cam_lyr = _rendered_cm.layers.get(_cm_cam_ln)
                            if not _cm_cam_lyr:
                                continue
                            # Pick pre-cached data URL
                            if _is_multi_cm and _cm_cam_lyr.color_svg_urls:
                                _cm_data_url = next(iter(_cm_cam_lyr.color_svg_urls.values()))
                            else:
                                _cm_data_url = _cm_cam_lyr.svg_data_url

                            # The CAM SVG covers the single-unit design.
                            # Shift its bounds so the unit's lower-left aligns with local (0, 0).
                            _cb_cm = _cm_cam_lyr.bounds   # (x0, y0, x1, y1) in board mm
                            _shift_x = -_cb_cm[0]
                            _shift_y = -_cb_cm[1]
                            _im_x    = _cb_cm[0] + _shift_x          # = 0
                            _im_y    = _cb_cm[3] + _shift_y           # = height of unit
                            _im_w    = _cb_cm[2] - _cb_cm[0]
                            _im_h    = _cb_cm[3] - _cb_cm[1]
                            _cm_fig.add_layout_image(dict(
                                source=_cm_data_url,
                                xref="x", yref="y",
                                x=_im_x, y=_im_y,
                                sizex=_im_w, sizey=_im_h,
                                sizing="stretch", layer="below",
                                opacity=0.95 if not _is_multi_cm else 0.7,
                            ))
                        # Tighten viewport to the actual CAM bounds
                        _cb0 = next(iter(_rendered_cm.layers.values())).bounds
                        _cm_cfg.board_bounds = (
                            _cb0[0] + _shift_x - 1.0,
                            _cb0[1] + _shift_y - 1.0,
                            _cb0[2] + _shift_x + 1.0,
                            _cb0[3] + _shift_y + 1.0,
                        )
                        from visualizer import _apply_layout as _cm_apply_layout
                        _cm_apply_layout(_cm_fig, _cm_cfg)

                    # Unit bounding rectangle
                    _cm_fig.add_shape(
                        type="rect",
                        x0=0, y0=0,
                        x1=_cam_cell_w, y1=_cam_cell_h,
                        line=dict(color="rgba(0,220,130,0.9)", width=2),
                        fillcolor="rgba(0,0,0,0)",
                        layer="above",
                    )

                    st.plotly_chart(
                        _cm_fig,
                        width='stretch',
                        config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False},
                    )

                    # ── Stats ─────────────────────────────────────────────────
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
                plot_bgcolor='#111111', paper_bgcolor='#1a1a1a',
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
                        plot_bgcolor='#111111', paper_bgcolor='#1a1a1a',
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
                            plot_bgcolor='#111111', paper_bgcolor='#1a1a1a',
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
