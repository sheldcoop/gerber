import json
import re
import tempfile
import threading
import time
from pathlib import Path

import streamlit as st
import pandas as pd

from odb_parser import parse_odb_archive
from gerber_renderer import render_odb_to_cam, load_render_cache, save_render_cache
from gerber_renderer import compute_tgz_digest
from aoi_loader import load_aoi_files, load_aoi_with_manual_side, FILENAME_PATTERN

def handle_bg_render_polling():
    """Check background render status and update state accordingly."""
    _prog_path = st.session_state.get('_render_progress_file')
    if _prog_path and Path(_prog_path).exists():
        try:
            _prog = json.loads(Path(_prog_path).read_text())
        except Exception:
            _prog = {'status': 'running'}

        if _prog.get('status') == 'done':
            # Render finished — load from cache
            _bg_tgz = st.session_state.pop('_render_tgz_bytes', None)
            _bg_digest = st.session_state.pop('_render_digest', None)
            _bg_name = st.session_state.pop('_render_filename', '')
            st.session_state.pop('_render_progress_file')
            Path(_prog_path).unlink(missing_ok=True)
            if _bg_tgz or _bg_digest:
                _bg_rendered = load_render_cache(digest=_bg_digest, tgz_bytes=_bg_tgz)
                if _bg_rendered:
                    st.session_state['rendered_odb'] = _bg_rendered
                    st.session_state['_tgz_bytes_for_cache'] = _bg_tgz
                    st.session_state['_tgz_digest'] = _bg_digest
                    _copper_layers = [l for l in _bg_rendered.layers.values() if l.layer_type != 'drill']
                    st.session_state['_panel_svgs_built'] = bool(_copper_layers) and all(
                        l.panel_svg_data_url for l in _copper_layers
                    )
                    st.session_state['data_loaded'] = True
                    st.rerun()

        elif _prog.get('status') == 'error':
            st.session_state.pop('_render_progress_file')
            Path(_prog_path).unlink(missing_ok=True)
            st.error(f"Background render failed: {_prog.get('error', 'unknown error')}")

        else:
            # Still running — show banner and schedule recheck
            st.info("Rendering CAM layers in background...")
            time.sleep(3)
            st.rerun()


def render_sidebar():
    """Renders the entire left-hand sidebar UI."""
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

        _aoi_key = st.session_state.get('_aoi_upload_key', 0)
        _aoi_col, _aoi_clear_col = st.columns([5, 1])
        with _aoi_col:
            aoi_files = st.file_uploader(
                "AOI Excel Files (.xlsx)",
                type=['xlsx', 'xls'],
                accept_multiple_files=True,
                help="Orbotech AOI defect data. Filename should follow BU-XXF / BU-XXB pattern",
                key=f"aoi_uploader_{_aoi_key}",
            )
        with _aoi_clear_col:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("🗑️ Reset", key="aoi_clear_btn", help="Clear all uploaded AOI files",
                         use_container_width=True):
                st.session_state['_aoi_upload_key'] = _aoi_key + 1
                for _k in ['aoi_dataset', 'needs_manual_side']:
                    st.session_state.pop(_k, None)
                st.rerun()

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
        load_btn = st.button("🔄 Load & Process", width='stretch', type="primary")

        if load_btn:
            parsed_odb = None
            aoi_dataset = None

            # Parse ODB++ archive
            if gerber_file:
                with st.spinner("Parsing ODB++ archive..."):
                    try:
                        parsed_odb = parse_odb_archive(
                            gerber_file.read(), gerber_file.name
                        )
                        gerber_file.seek(0)
                        st.session_state['parsed_odb'] = parsed_odb
                    except Exception as e:
                        st.error(f"ODB++ parsing failed: {e}")

                # Render CAM-quality SVGs via Gerbonara (with disk cache + background worker)
                if gerber_file:
                    try:
                        gerber_file.seek(0)
                        _tgz_bytes = gerber_file.read()
                        gerber_file.seek(0)

                        # Compute digest once — stored in session state so subsequent
                        # re-runs never re-hash the full archive.
                        _tgz_digest = compute_tgz_digest(_tgz_bytes)
                        st.session_state['_tgz_digest'] = _tgz_digest

                        rendered = load_render_cache(digest=_tgz_digest)
                        _from_cache = rendered is not None

                        if rendered:
                            # Cache hit — load instantly
                            st.session_state['rendered_odb'] = rendered
                            st.session_state['_tgz_bytes_for_cache'] = _tgz_bytes
                            st.session_state['_tgz_digest'] = _tgz_digest
                            _copper_lyrs = [l for l in rendered.layers.values() if l.layer_type != 'drill']
                            _svgs_ready = _from_cache and bool(_copper_lyrs) and all(
                                l.panel_svg_data_url for l in _copper_lyrs
                            )
                            st.session_state['_panel_svgs_built'] = _svgs_ready

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
                                        f"CAM render: {len(rendered.layers)} layers, "
                                        f"{total_features:,} features"
                                    )
                            for w in rendered.warnings:
                                st.warning(w, icon="⚠️")

                        else:
                            # Not cached — start background render thread
                            _prog_file = Path(tempfile.mktemp(suffix='_render.json'))
                            _prog_file.write_text('{"status":"running"}')

                            def _bg_render(_bytes=_tgz_bytes, _digest=_tgz_digest, _name=gerber_file.name, _pf=_prog_file):
                                try:
                                    r = render_odb_to_cam(_bytes, _name, digest=_digest)
                                    save_render_cache(r, digest=_digest)
                                    _pf.write_text('{"status":"done"}')
                                except Exception as e:
                                    _pf.write_text(json.dumps({"status": "error", "error": str(e)}))

                            threading.Thread(target=_bg_render, daemon=True).start()
                            st.session_state['_render_progress_file'] = str(_prog_file)
                            st.session_state['_render_tgz_bytes'] = _tgz_bytes
                            st.session_state['_render_digest'] = _tgz_digest
                            st.session_state['_render_filename'] = gerber_file.name
                            st.info("Rendering CAM layers in background — the page will update automatically when ready.")

                    except Exception as e:
                        st.warning(f"CAM rendering failed: {e}")

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
            st.divider()
            st.header("Coordinate Alignment")

            # Grid derived from TGZ step-repeat; fallback constants used only when
            # AOI data lacks UNIT_INDEX_Y/X columns.
            quad_rows = int(st.session_state.get('quad_rows_input', 6))
            quad_cols = int(st.session_state.get('quad_cols_input', 6))

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

            if 'manual_offset_x' not in st.session_state:
                st.session_state['manual_offset_x'] = 0.0
            if 'manual_offset_y' not in st.session_state:
                st.session_state['manual_offset_y'] = 0.0

            col1, col2 = st.columns(2)
            with col1:
                offset_x = st.number_input("X Offset (mm)", step=0.1, key='manual_offset_x')
            with col2:
                offset_y = st.number_input("Y Offset (mm)", step=0.1, key='manual_offset_y')

            st.session_state['align_args'] = {
                'manual_offset_x': offset_x,
                'manual_offset_y': offset_y,
            }

            st.divider()

            # ---- Section 2: Layer Controls ----
            _rendered_for_ctrl = st.session_state.get('rendered_odb')
            if _rendered_for_ctrl and _rendered_for_ctrl.layers:
                st.header("2. Layer Controls")

                visible_layers = []
                layer_opacities = {}

                def _layer_row(layer_name, layer, default_visible):
                    col1, col2 = st.columns([1, 2])
                    with col1:
                        visible = st.checkbox(
                            layer_name,
                            value=default_visible,
                            key=f"vis_{layer_name}",
                            help=f"{layer.layer_type} — {layer.feature_count} features",
                        )
                    with col2:
                        st.slider(
                            "Opacity", 0.0, 1.0, 0.40, step=0.05,
                            key=f"opacity_{layer_name}",
                            label_visibility="collapsed",
                        )
                    return visible

                def _copper_sort_key(name: str) -> int:
                    n = name.upper()
                    # Soldermask front
                    if 'FSR' in n or ('MASK' in n and 'F' in n and 'B' not in n): return 0
                    if n.startswith('3F') or n == '3F': return 10
                    if n.startswith('2F') or n == '2F': return 20
                    if '1FCO' in n: return 30
                    if '1BCO' in n: return 40
                    if n.startswith('2B') or n == '2B': return 50
                    if n.startswith('3B') or n == '3B': return 60
                    if 'BSR' in n or ('MASK' in n and 'B' in n): return 70
                    return 99

                def _drill_sort_key(name: str) -> tuple:
                    nums = re.findall(r'\d+', name)
                    return (int(nums[0]), int(nums[1])) if len(nums) >= 2 else (99, 99)

                copper_layers = dict(sorted(
                    ((n, l) for n, l in _rendered_for_ctrl.layers.items()
                     if l.layer_type in ('copper', 'signal', 'power', 'mixed')),
                    key=lambda kv: _copper_sort_key(kv[0])
                ))
                soldermask_layers = dict(sorted(
                    ((n, l) for n, l in _rendered_for_ctrl.layers.items()
                     if l.layer_type == 'soldermask'),
                    key=lambda kv: _copper_sort_key(kv[0])
                ))
                drill_layers = dict(sorted(
                    ((n, l) for n, l in _rendered_for_ctrl.layers.items()
                     if l.layer_type == 'drill'),
                    key=lambda kv: _drill_sort_key(kv[0])
                ))

                with st.expander(f"Copper ({len(copper_layers)})", expanded=True):
                    for i, (layer_name, layer) in enumerate(copper_layers.items()):
                        # Only the first (outermost) copper layer on by default
                        if _layer_row(layer_name, layer, i == 0):
                            visible_layers.append(layer_name)
                        layer_opacities[layer_name] = st.session_state.get(f"opacity_{layer_name}", 0.40)

                with st.expander(f"Soldermask ({len(soldermask_layers)})", expanded=False):
                    for layer_name, layer in soldermask_layers.items():
                        if _layer_row(layer_name, layer, False):
                            visible_layers.append(layer_name)
                        layer_opacities[layer_name] = st.session_state.get(f"opacity_{layer_name}", 0.40)

                with st.expander(f"Drill / Via ({len(drill_layers)})", expanded=False):
                    for layer_name, layer in drill_layers.items():
                        if _layer_row(layer_name, layer, False):
                            visible_layers.append(layer_name)
                        layer_opacities[layer_name] = st.session_state.get(f"opacity_{layer_name}", 0.40)

                st.toggle(
                    "⬛ Invert polarity",
                    key="invert_polarity",
                    value=False,
                    help="Swap copper and background colours — useful for checking negative-polarity layers",
                )

                st.radio(
                    "Panel background format",
                    ["SVG (vector)", "PNG (raster, faster zoom)"],
                    key="panel_bg_format",
                    horizontal=True,
                    help="PNG converts the panel SVG to a flat image once — faster to zoom/pan. SVG is sharper at any zoom.",
                )

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
                    ['by_type', 'by_verification', 'by_buildup', 'by_severity', 'by_source'],
                    format_func=lambda x: {
                        'by_type': 'By Defect Type',
                        'by_verification': 'By Verification',
                        'by_buildup': 'By Buildup',
                        'by_severity': 'By Severity',
                        'by_source': '📁 By Panel Source',
                    }.get(x, x),
                    key='color_mode_select',
                )

            # ---- Background Source ----
            st.session_state['bg_source'] = 'CAM (Gerbonara)'
