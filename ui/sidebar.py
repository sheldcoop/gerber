import json
import re
import tempfile
import threading
import time
from pathlib import Path

import streamlit as st
import pandas as pd

from odb_parser import parse_odb_archive
from gerber_renderer import render_odb_to_cam, load_render_cache, save_render_cache, clear_render_cache
from gerber_renderer import compute_tgz_digest, scan_available_layers
from aoi_loader import load_aoi_files, load_aoi_with_manual_side, FILENAME_PATTERN
from core.svg_utils import stable_layer_colors
from core.constants import copper_order_index
from core.cache import compose_render_key

# Copper-family layer types (rendered + selected by default).
_COPPER_RENDER_TYPES = ('copper', 'signal', 'power', 'mixed')


@st.cache_data(show_spinner=False)
def _scan_layers_cached(digest: str, _data: bytes):
    """Cheap layer enumeration (reads matrix only, ~0.1s), cached by tgz digest.

    ``_data`` is underscore-prefixed so Streamlit skips hashing the full archive
    every rerun — ``digest`` is the cache key.
    """
    return scan_available_layers(_data)


@st.cache_data(show_spinner=False, max_entries=3)
def _parse_odb_cached(digest: str, _data: bytes, filename: str):
    """Full ODB++ parse, cached by tgz digest (same pattern as _scan_layers_cached).

    Re-clicking Load & Process — e.g. after adding AOI files — skips the
    multi-second re-parse of an unchanged archive. ``max_entries`` bounds memory
    to a few boards; the copy-on-return cost only occurs on Load clicks.
    """
    return parse_odb_archive(_data, filename)


@st.cache_data(ttl=60, show_spinner=False)
def _cache_size_cached():
    """Disk-cache size for the sidebar caption — rglob'ing the cache dir on every
    rerun is wasteful. The Clear All Cache button calls st.cache_data.clear(),
    which flushes this too, so the caption refreshes right after a wipe."""
    from core.cache import get_cache_size
    return get_cache_size()


def _default_render_selected(layer_type: str) -> bool:
    """Default picker state: copper + soldermask on, drill/via off (the slow ones)."""
    return layer_type in _COPPER_RENDER_TYPES or layer_type == 'soldermask'


def _copper_sort_key(name: str) -> int:
    # Front soldermask first, back soldermask last; copper in physical stackup
    # order 4F,3F,2F,1FCO,1BCO,2B,3B,4B (single source: constants).
    n = name.upper()
    if 'FSR' in n or ('MASK' in n and 'F' in n and 'B' not in n):
        return -1
    if 'BSR' in n or ('MASK' in n and 'B' in n):
        return 999
    idx = copper_order_index(n)
    return idx if idx is not None else 99


def _drill_sort_key(name: str) -> tuple:
    nums = re.findall(r'\d+', name)
    return (int(nums[0]), int(nums[1])) if len(nums) >= 2 else (99, 99)


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

        # ---- Cache Management ----
        from core.cache import get_cache_size
        _cache_bytes, _cache_size = _cache_size_cached()

        _cache_col1, _cache_col2 = st.columns([3, 2])
        with _cache_col1:
            if st.button("🗑️ Clear All Cache", use_container_width=True, help="Clear all cached data including renders, computations, and AOI data"):
                _before_bytes, _before_size = get_cache_size()
                # Clear Streamlit cache
                st.cache_data.clear()
                # Clear disk render cache
                clear_render_cache()
                # Clear session state
                for key in list(st.session_state.keys()):
                    if key not in ['_aoi_upload_key']:  # Preserve upload widget key
                        del st.session_state[key]
                _after_bytes, _after_size = get_cache_size()
                if _before_bytes > 0:
                    st.toast(f"✅ Cache cleared: {_before_size} → {_after_size}", icon="🗑️")
                else:
                    st.toast("✅ All caches cleared!", icon="🗑️")
                st.rerun()
        
        with _cache_col2:
            if st.button("🔄 Refresh", use_container_width=True, help="Refresh the current view"):
                st.rerun()
        
        # Display cache size
        if _cache_bytes > 0:
            st.caption(f"💾 Cache: {_cache_size}")
        else:
            st.caption("💾 Cache: Empty")
        
        st.divider()

        # ---- Section 1: File Upload ----
        st.header("1. Upload Files")

        gerber_file = st.file_uploader(
            "ODB++ Archive (.tgz)",
            type=['tgz', 'gz'],
            help="Compressed ODB++ archive exported from InCam Pro",
        )

        # ── Layer render selection — populated cheaply from the matrix (pre-render) ──
        # Lets the user skip the heavy drill/via layers so only what they need is
        # parsed + rendered. Feeds `layer_filter` into the render at load time.
        if gerber_file:
            # Scan once per uploaded file (keyed by Streamlit's stable file_id) so a
            # checkbox click in the picker doesn't re-hash/re-extract the whole archive.
            _fid = getattr(gerber_file, 'file_id', None) or f"{gerber_file.name}:{gerber_file.size}"
            if st.session_state.get('_scan_fid') == _fid:
                _scanned = st.session_state.get('_scanned_layers', [])
            else:
                try:
                    _scan_bytes = gerber_file.getvalue()
                    _scanned = _scan_layers_cached(compute_tgz_digest(_scan_bytes), _scan_bytes)
                except Exception as _scan_err:
                    _scanned = []
                    st.caption(f"⚠️ Could not scan layers: {_scan_err}")
                st.session_state['_scan_fid'] = _fid
                st.session_state['_scanned_layers'] = _scanned

            if _scanned:
                _cu = sorted((nt for nt in _scanned if nt[1] in _COPPER_RENDER_TYPES),
                             key=lambda nt: _copper_sort_key(nt[0]))
                _sm = sorted((nt for nt in _scanned if nt[1] == 'soldermask'),
                             key=lambda nt: _copper_sort_key(nt[0]))
                _dr = sorted((nt for nt in _scanned if nt[1] == 'drill'),
                             key=lambda nt: _drill_sort_key(nt[0]))
                _seen = set(_cu) | set(_sm) | set(_dr)
                _other = [nt for nt in _scanned if nt not in _seen]

                with st.expander("🗂️ Layers to render", expanded=True):
                    st.caption(
                        "Pick which layers to render. Drill / via layers are the "
                        "slowest — leave them off for a faster load, turn them on only "
                        "when you need them."
                    )

                    def _sel_group(title, items):
                        if not items:
                            return
                        st.markdown(f"**{title}**")
                        for _n, _t in items:
                            st.checkbox(
                                _n,
                                value=st.session_state.get(
                                    f"render_sel_{_n}", _default_render_selected(_t)),
                                key=f"render_sel_{_n}",
                                help=f"type: {_t}",
                            )

                    _sel_group(f"Copper ({len(_cu)})", _cu)
                    _sel_group(f"Soldermask ({len(_sm)})", _sm)
                    _sel_group(f"Drill / Via ({len(_dr)})", _dr)
                    _sel_group(f"Other ({len(_other)})", _other)

                    _sel_now = [
                        n for n, t in _scanned
                        if st.session_state.get(f"render_sel_{n}", _default_render_selected(t))
                    ]
                    _has_copper = any(
                        t in _COPPER_RENDER_TYPES for n, t in _scanned if n in set(_sel_now)
                    )
                    if not _has_copper:
                        st.warning(
                            "No copper layer selected — board centering may be off.")
                    st.caption(
                        f"{len(_sel_now)} of {len(_scanned)} layers selected to render.")

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

        # ── Per-file classification UI ─────────────────────────────────────
        if aoi_files:
            with st.expander("📋 Classify Files", expanded=True):
                st.caption("Auto-filled from filename — adjust if needed before loading.")
                from aoi_loader import _parse_filename as _pfn
                _SIDES = ["Front", "Back"]
                _classifications = []

                for _i, _uf in enumerate(aoi_files):
                    _auto = _pfn(_uf.name)
                    # _auto returns (buildup, side, panel_id, section, warnings)
                    _auto_bu      = _auto[0] or 1
                    _auto_side    = 'Front' if (_auto[1] or 'F') == 'F' else 'Back'
                    _auto_panel   = int((_auto[2] or 'Panel_01').split('_')[-1])
                    _auto_section = _auto[3] or 1

                    st.markdown(f"📄 `{_uf.name}`")
                    _cc1, _cc2, _cc3, _cc4 = st.columns(4)

                    _panel = _cc1.number_input(
                        "Panel", min_value=1, max_value=99,
                        value=_auto_panel, key=f"cl_panel_{_i}",
                    )
                    _buildup = _cc2.number_input(
                        "Buildup", min_value=1, max_value=99,
                        value=_auto_bu, key=f"cl_bu_{_i}",
                    )
                    _side = _cc3.selectbox(
                        "Side", _SIDES,
                        index=0 if _auto_side == 'Front' else 1,
                        key=f"cl_side_{_i}",
                    )
                    _section = _cc4.number_input(
                        "Section", min_value=1, max_value=99,
                        value=_auto_section, key=f"cl_sec_{_i}",
                    )

                    _classifications.append({
                        'file'    : _uf,
                        'panel'   : int(_panel),
                        'buildup' : int(_buildup),
                        'side'    : _side,
                        'section' : int(_section),
                    })

                st.session_state['aoi_classifications'] = _classifications
        else:
            st.session_state['aoi_classifications'] = []

        st.divider()

        # ---- Load & Process Button ----
        load_btn = st.button("🔄 Load & Process", use_container_width=True, type="primary")

        if load_btn:
            parsed_odb = None
            aoi_dataset = None

            # Parse ODB++ archive
            if gerber_file:
                # Bytes + digest computed ONCE — the parse cache and the render
                # cache below both key off this digest.
                _tgz_bytes = gerber_file.getvalue()
                _raw_digest = compute_tgz_digest(_tgz_bytes)

                with st.spinner("Parsing ODB++ archive..."):
                    try:
                        parsed_odb = _parse_odb_cached(_raw_digest, _tgz_bytes, gerber_file.name)
                        st.session_state['parsed_odb'] = parsed_odb
                    except Exception as e:
                        st.error(f"ODB++ parsing failed: {e}")

                # Render CAM-quality SVGs via Gerbonara (with disk cache + background worker)
                if gerber_file:
                    try:
                        # Render only the layers ticked in the picker. The cache key
                        # folds in the selection so each layer-combination caches
                        # independently; selecting "all" keeps the plain digest, so
                        # existing full-render caches still hit.
                        _scanned = st.session_state.get('_scanned_layers', [])
                        _selected_layers = [
                            n for n, t in _scanned
                            if st.session_state.get(f"render_sel_{n}", _default_render_selected(t))
                        ] or None
                        _render_key = compose_render_key(_raw_digest, _selected_layers)
                        st.session_state['_render_key'] = _render_key
                        # The active render's on-disk identity (used by cache save/load).
                        st.session_state['_tgz_digest'] = _render_key

                        rendered = load_render_cache(digest=_render_key)
                        _from_cache = rendered is not None

                        if rendered:
                            # Cache hit — load instantly
                            st.session_state['rendered_odb'] = rendered
                            st.session_state['_tgz_bytes_for_cache'] = _tgz_bytes
                            st.session_state['_tgz_digest'] = _render_key
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

                            def _bg_render(_bytes=_tgz_bytes, _digest=_render_key, _name=gerber_file.name, _pf=_prog_file, _lf=_selected_layers):
                                try:
                                    r = render_odb_to_cam(_bytes, _name, layer_filter=_lf, digest=_digest)
                                    save_render_cache(r, digest=_digest)
                                    _pf.write_text('{"status":"done"}')
                                except Exception as e:
                                    _pf.write_text(json.dumps({"status": "error", "error": str(e)}))

                            threading.Thread(target=_bg_render, daemon=True).start()
                            st.session_state['_render_progress_file'] = str(_prog_file)
                            st.session_state['_render_tgz_bytes'] = _tgz_bytes
                            st.session_state['_render_digest'] = _render_key
                            st.session_state['_render_filename'] = gerber_file.name
                            st.info("Rendering CAM layers in background — the page will update automatically when ready.")

                    except Exception as e:
                        st.warning(f"CAM rendering failed: {e}")

            # Load AOI data
            if aoi_files:
                with st.spinner("Loading AOI defect data..."):
                    try:
                        _cls = st.session_state.get('aoi_classifications', [])
                        aoi_dataset = load_aoi_files(aoi_files, classifications=_cls if _cls else None)

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

                # Stable name→hue map shared with the overlay so the swatch next to a
                # layer always matches the colour drawn on screen.
                _layer_colors = stable_layer_colors(_rendered_for_ctrl.layers.keys())
                _all_layer_names = list(_rendered_for_ctrl.layers.keys())

                def _solo_cb(target):
                    # Show only this layer (turn everything else off).
                    def cb():
                        for _ln in _all_layer_names:
                            st.session_state[f"vis_{_ln}"] = (_ln == target)
                    return cb

                def _layer_row(layer_name, layer, default_visible):
                    swatch_col, chk_col, sld_col, solo_col = st.columns([0.4, 1.2, 1.8, 0.5])
                    with swatch_col:
                        _hue = _layer_colors.get(layer_name, '#888888')
                        st.markdown(
                            f"<div style='width:14px;height:14px;border-radius:3px;"
                            f"margin-top:6px;background:{_hue};"
                            f"border:1px solid #333;'></div>",
                            unsafe_allow_html=True,
                        )
                    with chk_col:
                        visible = st.checkbox(
                            layer_name,
                            value=default_visible,
                            key=f"vis_{layer_name}",
                            help=f"{layer.layer_type} — {layer.feature_count} features",
                        )
                    with sld_col:
                        st.slider(
                            "Opacity", 0.0, 1.0, 0.85, step=0.05,
                            key=f"opacity_{layer_name}",
                            label_visibility="collapsed",
                        )
                    with solo_col:
                        st.button("◉", key=f"solo_{layer_name}",
                                  on_click=_solo_cb(layer_name),
                                  help="Show only this layer")
                    return visible

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
                        layer_opacities[layer_name] = st.session_state.get(f"opacity_{layer_name}", 0.85)

                with st.expander(f"Soldermask ({len(soldermask_layers)})", expanded=False):
                    for layer_name, layer in soldermask_layers.items():
                        if _layer_row(layer_name, layer, False):
                            visible_layers.append(layer_name)
                        layer_opacities[layer_name] = st.session_state.get(f"opacity_{layer_name}", 0.85)

                with st.expander(f"Drill / Via ({len(drill_layers)})", expanded=False):
                    for layer_name, layer in drill_layers.items():
                        if _layer_row(layer_name, layer, False):
                            visible_layers.append(layer_name)
                        layer_opacities[layer_name] = st.session_state.get(f"opacity_{layer_name}", 0.85)

                st.toggle(
                    "⬛ Invert polarity",
                    key="invert_polarity",
                    value=False,
                    help="Swap copper and background colours — useful for checking negative-polarity layers",
                )

                st.divider()

            # ---- Section 3: Defect Filters ----
            aoi = st.session_state.get('aoi_dataset')
            if aoi and aoi.has_data:
                # We keep only Display Options in the navigation rail
                st.header("3. Display Options")
                marker_style = st.selectbox(
                    "Marker Style",
                    ['crosshair', 'dot', 'x_mark'],
                    format_func=lambda x: x.replace('_', ' ').title(),
                    key='marker_style_select',
                )

                # Color mode: only By Verification and By Defect Type. Default to verification
                # when the AOI data actually carries verification codes, else defect type.
                _has_verif = bool(
                    aoi and aoi.has_data
                    and 'VERIFICATION' in aoi.all_defects.columns
                    and aoi.all_defects['VERIFICATION'].notna().any()
                )
                _cm_options = ['by_verification', 'by_type']
                color_mode = st.selectbox(
                    "Color Mode",
                    _cm_options,
                    index=0 if _has_verif else 1,
                    format_func=lambda x: {
                        'by_type': 'By Defect Type',
                        'by_verification': 'By Verification',
                    }.get(x, x),
                    key='color_mode_select',
                )

                st.divider()

            # ---- Background Source ----
            st.session_state['bg_source'] = 'CAM (Gerbonara)'
