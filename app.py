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

import json
import re
import tempfile
import threading
import time
from pathlib import Path

import streamlit as st
import pandas as pd

from odb_parser import parse_odb_archive, ParsedODB
from gerber_renderer import render_odb_to_cam, RenderedODB, PanelLayout, save_render_cache, load_render_cache, build_panel_pngs
from aoi_loader import (
    load_aoi_files, load_aoi_with_manual_side,
    AOIDataset, FILENAME_PATTERN,
)
from alignment import (
    get_debug_info, AlignmentResult,
    compute_alignment_cached, apply_alignment_cached, _dict_to_alignment_result,
    compute_dataframe_hash,
    get_panel_quadrant_bounds,
    calculate_geometry, FRAME_WIDTH, FRAME_HEIGHT, INTER_UNIT_GAP,
)
from visualizer import build_defect_only_figure, OverlayConfig, _apply_layout
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
    local_rendered = st.session_state.get('rendered_odb')
    if not local_rendered:
        return

    b_list = st.session_state.get('buildup_filter_select', [])
    side_str = st.session_state.get('side_filter_select', 'Both')

    for name, lyr in local_rendered.layers.items():
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
# Background render polling
# ---------------------------------------------------------------------------

_prog_path = st.session_state.get('_render_progress_file')
if _prog_path and Path(_prog_path).exists():
    try:
        _prog = json.loads(Path(_prog_path).read_text())
    except Exception:
        _prog = {'status': 'running'}

    if _prog.get('status') == 'done':
        # Render finished — load from cache
        _bg_tgz = st.session_state.pop('_render_tgz_bytes', None)
        _bg_name = st.session_state.pop('_render_filename', '')
        st.session_state.pop('_render_progress_file')
        Path(_prog_path).unlink(missing_ok=True)
        if _bg_tgz:
            _bg_rendered = load_render_cache(_bg_tgz)
            if _bg_rendered:
                st.session_state['rendered_odb'] = _bg_rendered
                st.session_state['_tgz_bytes_for_cache'] = _bg_tgz
                _copper_layers = [l for l in _bg_rendered.layers.values() if l.layer_type != 'drill']
                st.session_state['_panel_pngs_built'] = bool(_copper_layers) and all(
                    l.panel_png_data_url for l in _copper_layers
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

                    pass  # parsed_odb used for alignment metadata only
                except Exception as e:
                    st.error(f"ODB++ parsing failed: {e}")

            # Render CAM-quality SVGs via Gerbonara (with disk cache + background worker)
            if gerber_file:
                try:
                    gerber_file.seek(0)
                    _tgz_bytes = gerber_file.read()
                    gerber_file.seek(0)

                    rendered = load_render_cache(_tgz_bytes)
                    _from_cache = rendered is not None

                    if rendered:
                        # Cache hit — load instantly
                        st.session_state['rendered_odb'] = rendered
                        st.session_state['_tgz_bytes_for_cache'] = _tgz_bytes
                        _copper_lyrs = [l for l in rendered.layers.values() if l.layer_type != 'drill']
                        _pngs_ready = _from_cache and bool(_copper_lyrs) and all(
                            l.panel_png_data_url for l in _copper_lyrs
                        )
                        st.session_state['_panel_pngs_built'] = _pngs_ready

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

                        def _bg_render(_bytes=_tgz_bytes, _name=gerber_file.name, _pf=_prog_file):
                            try:
                                r = render_odb_to_cam(_bytes, _name)
                                save_render_cache(_bytes, r)
                                _pf.write_text('{"status":"done"}')
                            except Exception as e:
                                _pf.write_text(json.dumps({"status": "error", "error": str(e)}))

                        threading.Thread(target=_bg_render, daemon=True).start()
                        st.session_state['_render_progress_file'] = str(_prog_file)
                        st.session_state['_render_tgz_bytes'] = _tgz_bytes
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
        with st.sidebar:
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
                 if l.layer_type in ('copper', 'soldermask')),
                key=lambda kv: _copper_sort_key(kv[0])
            ))
            drill_layers = dict(sorted(
                ((n, l) for n, l in _rendered_for_ctrl.layers.items()
                 if l.layer_type == 'drill'),
                key=lambda kv: _drill_sort_key(kv[0])
            ))

            with st.expander(f"Copper & Soldermask ({len(copper_layers)})", expanded=True):
                for i, (layer_name, layer) in enumerate(copper_layers.items()):
                    # Only the first (outermost) copper layer on by default
                    if _layer_row(layer_name, layer, i == 0):
                        visible_layers.append(layer_name)
                    layer_opacities[layer_name] = st.session_state.get(f"opacity_{layer_name}", 0.40)

            with st.expander(f"Drill / Via ({len(drill_layers)})", expanded=False):
                for layer_name, layer in drill_layers.items():
                    if _layer_row(layer_name, layer, False):
                        visible_layers.append(layer_name)
                    layer_opacities[layer_name] = st.session_state.get(f"opacity_{layer_name}", 0.40)

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
        st.session_state['bg_source'] = 'CAM (Gerbonara)'


# ---------------------------------------------------------------------------
# Cached helpers (pure functions — recompute only when inputs change)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _compute_clusters_cached(_df_hash: str, _df: pd.DataFrame, eps: float, min_samples: int):
    """Run DBSCAN + summary + all hull coords. Cached by df hash + params."""
    from clustering import compute_clusters, get_cluster_summary, get_cluster_hull_coords
    clustered = compute_clusters(_df, eps=eps, min_samples=min_samples)
    summary = get_cluster_summary(clustered)
    hulls = {}
    if not summary.empty:
        for _, crow in summary.iterrows():
            h = get_cluster_hull_coords(clustered, crow['cluster_id'])
            if h:
                hulls[crow['cluster_id']] = (h, crow['defect_count'])
    return clustered, summary, hulls


@st.cache_data(show_spinner=False)
def _compute_panel_shapes(rows: int, cols: int, gap_x: float, gap_y: float) -> list:
    """Pre-compute all unit cell shape dicts. Cached per grid geometry."""
    from alignment import calculate_geometry, INTER_UNIT_GAP
    ctx = calculate_geometry(rows, cols, gap_x, gap_y)
    shapes = []
    for _, (q_ox, q_oy) in ctx.quadrant_origins.items():
        for r in range(rows):
            for c in range(cols):
                ux = q_ox + INTER_UNIT_GAP + c * ctx.stride_x
                uy = q_oy + INTER_UNIT_GAP + r * ctx.stride_y
                shapes.append(dict(
                    type="rect",
                    x0=ux, y0=uy,
                    x1=ux + ctx.cell_width, y1=uy + ctx.cell_height,
                    fillcolor="rgba(0,180,100,0.07)",
                    line=dict(color="rgba(0,220,130,0.5)", width=0.8),
                    layer="below",
                ))
    return shapes


# ── Coordinate system reference ───────────────────────────────────────────────
#
# THREE coordinate spaces are in play. Understanding them is critical.
#
# 1. ODB++ RAW space  (unit_positions_raw)
#    Origin: centre of the ODB++ panel frame (0, 0 = panel centre).
#    Values: NEGATIVE for most units, e.g. bottom-left unit ≈ (-207mm, -218mm).
#    Used for: step-repeat parsing inside gerber_renderer only.
#    *** NEVER use these values for AOI alignment. ***
#
# 2. ODB++ DISPLAY space  (unit_positions  ←  what we use here)
#    Origin: bottom-left corner of the 510×515mm panel frame (0, 0 = panel corner).
#    Values: POSITIVE, ranging from ~23mm to ~460mm for a 12×12 panel.
#    Computed by: gerber_renderer.compute_unit_positions() — shifts raw coords
#                 so content is centred inside the physical panel frame.
#    This is the STEP ORIGIN for each unit (where the ODB++ step-repeat places it).
#
# 3. AOI machine space  (X_MM / Y_MM columns in the Excel file)
#    Origin: bottom-left corner of the same panel frame (matches display space).
#    Values: POSITIVE, empirically confirmed to start at unit_positions_y for
#            each unit row (e.g. row-0 defects start at Y_MM ≈ 23.1mm = unit_pos_y).
#    Key insight: AOI measures from the STEP ORIGIN, not from the bottom of the
#                 CAM features. Because the CAM design is CENTRED at the step origin
#                 (cam_min_y ≈ -16mm, cam_max_y ≈ +16mm), features below the step
#                 origin exist in ODB++ but the AOI Y reference starts AT the origin.
#
# ALIGNMENT FORMULA (Commonality and SUI):
#    ALIGNED_X = X_MM  - unit_pos_x     (subtract step-origin X)
#    ALIGNED_Y = Y_MM  - unit_pos_y     (subtract step-origin Y)
#    Result range: [0, cell_w] × [0, cell_h]  — matches the CAM SVG in Plotly space.
#
# CAM SVG PLOTLY PLACEMENT:
#    The SVG has local coords [cam_min_x, cam_max_x] × [cam_min_y, cam_max_y].
#    Plotly places it at x=0, y=cell_h, sizex=cell_w, sizey=cell_h.
#    This maps  cam_min → Plotly 0  and  cam_max → Plotly cell_w/cell_h.
#    A feature at local (lx, ly) appears at Plotly (lx-cam_min_x, ly-cam_min_y).
#    A defect at ALIGNED (ax, ay) = (X_MM-unit_pos_x, Y_MM-unit_pos_y) maps to
#    the same Plotly position, so dots land on copper. ✓
#
# WHY NOT unit_pos + cam_min?
#    Subtracting (unit_pos + cam_min) shifts defects UP by |cam_min| ≈ 16mm,
#    pushing ~half of all defects above the visible CAM area. Verified against
#    actual Excel data: Y_MM_min per unit row == unit_positions_y (not +cam_min).
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _compute_cm_geometry(
    unit_positions: tuple,       # tuple of (x, y) — ODB++ display (panel-absolute) coords
    first_layer_bounds: tuple,   # (min_x, min_y, max_x, max_y) of CAM layer in local space
) -> tuple:
    """Return (origins_dict, cell_w, cell_h). Cached per unique TGZ layout.

    origins_dict maps (row_index, col_index) → (origin_x, origin_y) where:
      - row_index / col_index are 0-based sorted position indices
      - origin_x/y = the unit's display position (step origin in panel space)

    To align a defect: ALIGNED = (X_MM - origin_x, Y_MM - origin_y)
    Result is in [0, cell_w] × [0, cell_h], matching the CAM SVG in Plotly.

    See the coordinate system reference comment above for full explanation.
    """
    cam_min_x, cam_min_y, cam_max_x, cam_max_y = first_layer_bounds
    cell_w = cam_max_x - cam_min_x
    cell_h = cam_max_y - cam_min_y
    uniq_x = sorted(set(round(x, 2) for x, _ in unit_positions))
    uniq_y = sorted(set(round(y, 2) for _, y in unit_positions))
    # Origin = display position only — NO cam_min offset.
    # AOI measures from the step origin; cam_min offset must NOT be subtracted.
    origins = {
        (ri, ci): (uniq_x[ci], uniq_y[ri])
        for ri in range(len(uniq_y))
        for ci in range(len(uniq_x))
    }
    return origins, cell_w, cell_h


def _filter_aoi_cm(
    _df: pd.DataFrame,
    buildup_filter: tuple,
    side_filter: tuple,
) -> pd.DataFrame:
    """Scope-filter AOI defects for Commonality. Cached by filter combo."""
    src = _df.copy()
    if buildup_filter and 'BUILDUP' in src.columns:
        src = src[src['BUILDUP'].isin(buildup_filter)]
    if 'SIDE' in src.columns:
        if 'Front' in side_filter and 'Back' not in side_filter:
            src = src[src['SIDE'] == 'F']
        elif 'Back' in side_filter and 'Front' not in side_filter:
            src = src[src['SIDE'] == 'B']
    return src


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

    _tabs = ["🔭 Panel Overview", "🗺️ Commonality"]
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
                _panel_png_url = None
                _panel_bg_name = None
                _want_layer = None
                _all_checked_panel = [
                    _ln for _ln in _rendered_panel.layers
                    if st.session_state.get(f"vis_{_ln}", False)
                ]
                if _all_checked_panel:
                    _want_layer = _all_checked_panel[0]   # PNG can only show one layer
                # Build panel PNG only for the selected layer (on-demand, one layer at a time)
                if _want_layer:
                    _want_lyr_obj = _rendered_panel.layers[_want_layer]
                    if not _want_lyr_obj.panel_png_data_url:
                        with st.spinner(f"Building panel image for {_want_layer}..."):
                            from gerber_renderer import build_panel_png_hires
                            try:
                                _want_lyr_obj.panel_png_data_url = build_panel_png_hires(
                                    _want_lyr_obj.svg_string, _rendered_panel.panel_layout
                                )
                            except Exception:
                                pass
                            _tgz_b = st.session_state.get('_tgz_bytes_for_cache')
                            if _tgz_b and _want_lyr_obj.panel_png_data_url:
                                save_render_cache(_tgz_b, _rendered_panel)
                    if _want_lyr_obj.panel_png_data_url:
                        _panel_png_url = _want_lyr_obj.panel_png_data_url
                        _panel_bg_name = _want_layer

                if _panel_png_url:
                    panel_fig.update_layout(images=[dict(
                        source=_panel_png_url,
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
                _cl_hash = compute_dataframe_hash(panel_df)
                clustered_df, cluster_summary, _hulls = _compute_clusters_cached(
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
            _cell_shapes = _compute_panel_shapes(_pq_rows, _pq_cols, _pd_gap_x, _pd_gap_y)
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
                # Use pre-cached panel PNG for the checked layer only (no fallback)
                _panel_png = None
                _sel_ln2 = None
                for _ln2 in _rodb.layers:
                    if st.session_state.get(f"vis_{_ln2}", False):
                        _sel_ln2 = _ln2
                        break

                if _sel_ln2:
                    _sel_lyr2 = _rodb.layers[_sel_ln2]
                    if not _sel_lyr2.panel_png_data_url:
                        with st.spinner(f"Building panel image for {_sel_ln2}..."):
                            from gerber_renderer import build_panel_png_hires
                            try:
                                _sel_lyr2.panel_png_data_url = build_panel_png_hires(
                                    _sel_lyr2.svg_string, _pl_cam
                                )
                            except Exception:
                                pass
                            _tgz_b2 = st.session_state.get('_tgz_bytes_for_cache')
                            if _tgz_b2 and _sel_lyr2.panel_png_data_url:
                                save_render_cache(_tgz_b2, _rodb)
                    _panel_png = _sel_lyr2.panel_png_data_url

                if not _panel_png:
                    st.caption("☝️ Select a layer in the sidebar to display the panel image.")

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

    # ── Commonality / Superposition View ────────────────────────────────────
    elif view_mode == "🗺️ Commonality":
        # Layer stacking helpers — copper always on top, drill/laser always at back.
        # Plotly renders layout_images in the order they are added; later = on top.
        _LAYER_Z = {'drill': 0, 'other': 1, 'paste': 2,
                    'soldermask': 3, 'silkscreen': 4, 'outline': 5, 'copper': 6}
        _LAYER_OPACITY_SINGLE = {'copper': 0.95, 'drill': 0.55, 'other': 0.60}
        _LAYER_OPACITY_MULTI  = {'copper': 0.90, 'drill': 0.45, 'other': 0.50}

        def _layer_sort_key(name_lyr_pair):
            return _LAYER_Z.get(name_lyr_pair[1].layer_type, 1)

        def _layer_opacity(layer_name, lyr_type, multi):
            # Sidebar slider takes priority; fall back to per-type defaults
            slider_val = st.session_state.get(f"opacity_{layer_name}")
            if slider_val is not None:
                return float(slider_val)
            d = _LAYER_OPACITY_MULTI if multi else _LAYER_OPACITY_SINGLE
            return d.get(lyr_type, 0.70 if multi else 0.85)
        st.markdown("### 🗺️ Commonality — Defect Superposition")
        st.caption("Normalise each selected unit's defects into local coordinates and overlay on a single reference unit.")

        _rodb_cm_check = st.session_state.get('rendered_odb')
        _has_aoi_cm = (
            aoi and aoi.has_data
            and 'UNIT_INDEX_X' in aoi.all_defects.columns
            and 'UNIT_INDEX_Y' in aoi.all_defects.columns
        )

        if not _rodb_cm_check and not _has_aoi_cm:
            st.info("Upload a TGZ design file or AOI defect data to use this view.")

        elif not _has_aoi_cm:
            # ── TGZ loaded but no AOI — show design reference only ────────────
            st.info("ℹ️ Upload AOI defect data to overlay defects on the design.")
            if _rodb_cm_check and _rodb_cm_check.layers:
                # Reference layer: first non-drill for consistent bounds
                _no_aoi_ref_lyr = next(
                    (l for l in _rodb_cm_check.layers.values() if l.layer_type != 'drill'),
                    next(iter(_rodb_cm_check.layers.values()))
                )
                # Compute cell dimensions from TGZ geometry
                if _rodb_cm_check.panel_layout:
                    _, _no_aoi_cw, _no_aoi_ch = _compute_cm_geometry(
                        unit_positions=tuple(_rodb_cm_check.panel_layout.unit_positions),
                        first_layer_bounds=tuple(_no_aoi_ref_lyr.bounds),
                    )
                else:
                    _rb_na = _no_aoi_ref_lyr.bounds
                    _no_aoi_cw = _rb_na[2] - _rb_na[0]
                    _no_aoi_ch = _rb_na[3] - _rb_na[1]

                # Collect ALL checked layers (no break — support multi-layer stack)
                _na_checked = [
                    (_na_n, _na_l)
                    for _na_n, _na_l in _rodb_cm_check.layers.items()
                    if st.session_state.get(f"vis_{_na_n}", False)
                ]

                if not _na_checked:
                    st.caption("☝️ Select a layer in the sidebar to view the design.")
                else:
                    _ref_b_na  = _no_aoi_ref_lyr.bounds
                    _ref_sx_na = -_ref_b_na[0]
                    _ref_sy_na = -_ref_b_na[1]
                    _is_multi_na = len(_na_checked) > 1
                    # Sort: drill/laser at back → copper on top
                    _na_sorted = sorted(_na_checked, key=_layer_sort_key)

                    _design_fig = go.Figure()
                    for _na_n, _na_l in _na_sorted:
                        _lyr_b_na = _na_l.bounds
                        _design_fig.add_layout_image(dict(
                            source=_na_l.svg_data_url,
                            xref="x", yref="y",
                            x=_lyr_b_na[0] + _ref_sx_na,
                            y=_lyr_b_na[3] + _ref_sy_na,
                            sizex=_lyr_b_na[2] - _lyr_b_na[0],
                            sizey=_lyr_b_na[3] - _lyr_b_na[1],
                            sizing="stretch", layer="below",
                            opacity=_layer_opacity(_na_n, _na_l.layer_type, _is_multi_na),
                        ))

                    # Layer label — show all active names
                    _lbl_na = " + ".join(n for n, _ in _na_checked)
                    # Dimension annotations
                    _design_fig.add_annotation(
                        x=_no_aoi_cw / 2, y=-_no_aoi_ch * 0.045,
                        text=f"W: {_no_aoi_cw:.2f} mm", showarrow=False,
                        font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
                        xref="x", yref="y",
                    )
                    _design_fig.add_annotation(
                        x=-_no_aoi_cw * 0.045, y=_no_aoi_ch / 2,
                        text=f"H: {_no_aoi_ch:.2f} mm", showarrow=False, textangle=-90,
                        font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
                        xref="x", yref="y",
                    )
                    # Layer name label (top-centre green)
                    _design_fig.add_annotation(
                        x=_no_aoi_cw / 2, y=_no_aoi_ch + _no_aoi_ch * 0.04,
                        text=f"Layer: {_lbl_na}",
                        showarrow=False, xanchor="center", yanchor="bottom",
                        font=dict(color="rgba(0,220,130,0.95)", size=12, family="monospace"),
                        xref="x", yref="y",
                    )
                    _design_fig.update_layout(
                        xaxis=dict(range=[-1, _no_aoi_cw + 1], scaleanchor='y', scaleratio=1,
                                   showgrid=False, zeroline=False, showticklabels=False),
                        yaxis=dict(range=[-1, _no_aoi_ch + 1], showgrid=False,
                                   zeroline=False, showticklabels=False),
                        plot_bgcolor='#000000', paper_bgcolor='#000000',
                        font=dict(color='#cccccc'),
                        margin=dict(l=0, r=0, t=36, b=0), height=600,
                    )
                    st.plotly_chart(_design_fig, width='stretch',
                                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})

        else:
            _q_rows_cm  = int(st.session_state.get('quad_rows_input', 6))
            _q_cols_cm  = int(st.session_state.get('quad_cols_input', 6))
            _d_gap_x_cm = float(st.session_state.get('dyn_gap_x_input', 5.0))
            _d_gap_y_cm = float(st.session_state.get('dyn_gap_y_input', 3.5))

            # ── Build full unit grid from TGZ (all 144 units), fall back to AOI ─
            _rodb_cm_pl = st.session_state.get('rendered_odb')
            if _rodb_cm_pl and _rodb_cm_pl.panel_layout:
                _pl_cm  = _rodb_cm_pl.panel_layout
                _rp_cm  = _pl_cm.unit_positions   # display (panel-absolute), same frame as AOI X_MM
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
            _qs_cm[0].button("ALL",   key="cm_all",   on_click=_cm_set(_all_cm_labels), width="stretch", type="primary")
            _qs_cm[1].button("Q1",    key="cm_q1",    on_click=_cm_set(_q1_cm_lbl),     width="stretch")
            _qs_cm[2].button("Q2",    key="cm_q2",    on_click=_cm_set(_q2_cm_lbl),     width="stretch")
            _qs_cm[3].button("Q3",    key="cm_q3",    on_click=_cm_set(_q3_cm_lbl),     width="stretch")
            _qs_cm[4].button("Q4",    key="cm_q4",    on_click=_cm_set(_q4_cm_lbl),     width="stretch")
            _qs_cm[5].button("Clear", key="cm_clear", on_click=_cm_set([]),             width="stretch")

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
                    # No TGZ loaded — origins unknown, defects will show at raw coords
                    _cm_origins = {}

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
                                    source=_em_l.svg_data_url,
                                    xref="x", yref="y",
                                    x=_lyr_b_em[0] + _ref_sx_em,
                                    y=_lyr_b_em[3] + _ref_sy_em,
                                    sizex=_lyr_b_em[2] - _lyr_b_em[0],
                                    sizey=_lyr_b_em[3] - _lyr_b_em[1],
                                    sizing="stretch", layer="below",
                                    opacity=_layer_opacity(_em_n, _em_l.layer_type, _is_multi_em),
                                ))
                            _em_lbl = " + ".join(n for n, _ in _em_checked)
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
                            _em_fig.update_layout(
                                xaxis=dict(range=[-1, _cam_cell_w + 1], scaleanchor='y', scaleratio=1,
                                           showgrid=False, zeroline=False, showticklabels=False),
                                yaxis=dict(range=[-1, _cam_cell_h + 1], showgrid=False,
                                           zeroline=False, showticklabels=False),
                                plot_bgcolor='#000000', paper_bgcolor='#000000',
                                font=dict(color='#cccccc'),
                                margin=dict(l=0, r=0, t=36, b=0), height=600,
                            )
                            st.plotly_chart(_em_fig, width='stretch',
                                            config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})
                else:
                    # ── Coordinate normalisation (vectorized) ─────────────────
                    # Subtract each unit's effective origin so all units fold into
                    # local coords [0…cell_w] × [0…cell_h], matching CAM SVG at (0,0).
                    # Normalise UNIT_INDEX to 0-based to match sorted-position indices.
                    _cm_min_iy = int(aoi.all_defects['UNIT_INDEX_Y'].min())
                    _cm_min_ix = int(aoi.all_defects['UNIT_INDEX_X'].min())
                    _pairs_cm = list(zip(
                        _cm_src['UNIT_INDEX_Y'].astype(int) - _cm_min_iy,
                        _cm_src['UNIT_INDEX_X'].astype(int) - _cm_min_ix,
                    ))
                    _ox_arr = [_cm_origins.get(p, (0.0, 0.0))[0] for p in _pairs_cm]
                    _oy_arr = [_cm_origins.get(p, (0.0, 0.0))[1] for p in _pairs_cm]

                    _cm_off_x = align_args.get('manual_offset_x', 0.0)
                    _cm_off_y = align_args.get('manual_offset_y', 0.0)
                    _cm_plot = _cm_src.copy()
                    _cm_plot['ALIGNED_X'] = _cm_src['X_MM'].values - _ox_arr + _cm_off_x
                    _cm_plot['ALIGNED_Y'] = _cm_src['Y_MM'].values - _oy_arr + _cm_off_y

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
                    _rendered_cm  = st.session_state.get('rendered_odb')

                    if _rendered_cm and _rendered_cm.layers:
                        # Pick checked layers only — if none checked, show no background
                        _cm_cam_layers = [
                            n for n in _rendered_cm.layers
                            if st.session_state.get(f"vis_{n}", False)
                        ]

                        _is_multi_cm = len(_cm_cam_layers) > 1
                        # Sort: drill/laser rendered first (back), copper last (front)
                        _cm_cam_pairs = [
                            (ln, _rendered_cm.layers[ln])
                            for ln in _cm_cam_layers
                            if _rendered_cm.layers.get(ln)
                        ]
                        _cm_cam_pairs.sort(key=_layer_sort_key)
                        _active_layer_name = _cm_cam_pairs[-1][0] if _cm_cam_pairs else None
                        # Use reference bounds (first non-drill layer) as the common
                        # alignment anchor — all SVGs placed relative to the same origin.
                        _ref_b_cm    = _first_lyr_cm.bounds
                        _ref_shift_x = -_ref_b_cm[0]
                        _ref_shift_y = -_ref_b_cm[1]
                        for _cm_cam_ln, _cm_cam_lyr in _cm_cam_pairs:
                            # Pick pre-cached data URL
                            if _is_multi_cm and _cm_cam_lyr.color_svg_urls:
                                _cm_data_url = next(iter(_cm_cam_lyr.color_svg_urls.values()))
                            else:
                                _cm_data_url = _cm_cam_lyr.svg_data_url

                            # Place using shared reference shift for alignment
                            _cb_cm = _cm_cam_lyr.bounds
                            _im_x  = _cb_cm[0] + _ref_shift_x
                            _im_y  = _cb_cm[3] + _ref_shift_y
                            _im_w  = _cb_cm[2] - _cb_cm[0]
                            _im_h  = _cb_cm[3] - _cb_cm[1]
                            _cm_fig.add_layout_image(dict(
                                source=_cm_data_url,
                                xref="x", yref="y",
                                x=_im_x, y=_im_y,
                                sizex=_im_w, sizey=_im_h,
                                sizing="stretch", layer="below",
                                opacity=_layer_opacity(_cm_cam_ln, _cm_cam_lyr.layer_type, _is_multi_cm),
                            ))
                        # Lock viewport to cell dimensions (same as green rect and SVG placement)
                        from visualizer import _apply_layout as _cm_apply_layout
                        _cm_apply_layout(_cm_fig, _cm_cfg)

                    # ── Subtle mm grid ────────────────────────────────────────
                    _grid_step = 5.0  # 5mm grid
                    import math as _math
                    _gx = _grid_step
                    while _gx < _cam_cell_w:
                        _cm_fig.add_shape(type="line",
                            x0=_gx, y0=0, x1=_gx, y1=_cam_cell_h,
                            line=dict(color="rgba(255,255,255,0.06)", width=1),
                            layer="below")
                        _gx += _grid_step
                    _gy = _grid_step
                    while _gy < _cam_cell_h:
                        _cm_fig.add_shape(type="line",
                            x0=0, y0=_gy, x1=_cam_cell_w, y1=_gy,
                            line=dict(color="rgba(255,255,255,0.06)", width=1),
                            layer="below")
                        _gy += _grid_step


                    # ── Dimension annotations (width × height) ────────────────
                    _cm_fig.add_annotation(
                        x=_cam_cell_w / 2, y=-_cam_cell_h * 0.045,
                        text=f"W: {_cam_cell_w:.2f} mm",
                        showarrow=False,
                        font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
                        xref="x", yref="y",
                    )
                    _cm_fig.add_annotation(
                        x=-_cam_cell_w * 0.045, y=_cam_cell_h / 2,
                        text=f"H: {_cam_cell_h:.2f} mm",
                        showarrow=False, textangle=-90,
                        font=dict(color="rgba(0,220,130,0.8)", size=11, family="monospace"),
                        xref="x", yref="y",
                    )

                    # ── Layer name label (top-centre, green) ─────────────────
                    if _active_layer_name:
                        _cm_fig.add_annotation(
                            x=_cam_cell_w / 2, y=_cam_cell_h + _cam_cell_h * 0.04,
                            text=f"Layer: {_active_layer_name}",
                            showarrow=False, xanchor="center", yanchor="bottom",
                            font=dict(color="rgba(0,220,130,0.95)", size=12, family="monospace"),
                            xref="x", yref="y",
                        )

                    # ── Hotspot ring (densest cluster centre) ─────────────────
                    if len(_cm_plot) >= 5:
                        try:
                            from sklearn.neighbors import KernelDensity
                            import numpy as _np2
                            _hs_xy = _cm_plot[['ALIGNED_X', 'ALIGNED_Y']].dropna().values
                            if len(_hs_xy) >= 5:
                                _kde = KernelDensity(bandwidth=1.5, kernel='gaussian')
                                _kde.fit(_hs_xy)
                                _nx, _ny = 40, 40
                                _gxv = _np2.linspace(0, _cam_cell_w, _nx)
                                _gyv = _np2.linspace(0, _cam_cell_h, _ny)
                                _gxx, _gyy = _np2.meshgrid(_gxv, _gyv)
                                _grid_pts = _np2.column_stack([_gxx.ravel(), _gyy.ravel()])
                                _dens = _np2.exp(_kde.score_samples(_grid_pts)).reshape(_ny, _nx)
                                _peak_idx = _np2.unravel_index(_dens.argmax(), _dens.shape)
                                _hs_cx = float(_gxv[_peak_idx[1]])
                                _hs_cy = float(_gyv[_peak_idx[0]])
                                _hs_r  = min(_cam_cell_w, _cam_cell_h) * 0.06
                                _cm_fig.add_shape(type="circle",
                                    x0=_hs_cx - _hs_r, y0=_hs_cy - _hs_r,
                                    x1=_hs_cx + _hs_r, y1=_hs_cy + _hs_r,
                                    line=dict(color="rgba(255,80,80,0.9)", width=2, dash="dot"),
                                    fillcolor="rgba(255,80,80,0.08)", layer="above")
                                _cm_fig.add_annotation(
                                    x=_hs_cx, y=_hs_cy + _hs_r + _cam_cell_h * 0.02,
                                    text="hotspot", showarrow=False,
                                    font=dict(color="rgba(255,100,100,0.9)", size=10, family="monospace"),
                                    xref="x", yref="y",
                                )
                        except Exception:
                            pass  # sklearn not available or not enough data

                    # ── Heatmap toggle ────────────────────────────────────────
                    _show_heatmap = st.toggle("🌡️ Density Heatmap", value=False,
                                              help="Overlay a 2D defect density heatmap instead of individual dots",
                                              key="cm_heatmap_toggle")
                    if _show_heatmap and len(_cm_plot) >= 3:
                        try:
                            import numpy as _np3
                            _hm_x = _cm_plot['ALIGNED_X'].dropna().values
                            _hm_y = _cm_plot['ALIGNED_Y'].dropna().values
                            _hm_nx, _hm_ny = 60, 60
                            _hm_gx = _np3.linspace(0, _cam_cell_w, _hm_nx)
                            _hm_gy = _np3.linspace(0, _cam_cell_h, _hm_ny)
                            _hm_z, _, _ = _np3.histogram2d(_hm_y, _hm_x,
                                bins=[_hm_ny, _hm_nx],
                                range=[[0, _cam_cell_h], [0, _cam_cell_w]])
                            from scipy.ndimage import gaussian_filter as _gf
                            _hm_z = _gf(_hm_z.astype(float), sigma=2.0)
                            _cm_fig.add_trace(go.Heatmap(
                                z=_hm_z,
                                x=_hm_gx, y=_hm_gy,
                                colorscale='Hot',
                                opacity=0.55,
                                showscale=False,
                                hoverinfo='skip',
                            ))
                        except Exception:
                            st.warning("Heatmap requires scipy. Install with: pip install scipy")

                    # ── "N defects from M units" subtitle ────────────────────
                    _n_def = len(_cm_plot)
                    _n_units = len(_cm_sel_units)
                    _cm_fig.update_layout(
                        title=dict(
                            text=f"{_n_def} defects · {_n_units} units · avg {_n_def/_n_units:.1f}/unit",
                            font=dict(color="rgba(180,180,180,0.8)", size=12, family="monospace"),
                            x=0.5, xanchor="center",
                        )
                    )

                    _export_col, _spacer = st.columns([1, 4])
                    with _export_col:
                        try:
                            from export import export_current_view
                            _cm_png = export_current_view(_cm_fig, fmt='png', scale=3)
                            st.download_button(
                                "📷 Export PNG",
                                data=_cm_png,
                                file_name="commonality_unit.png",
                                mime="image/png",
                                width="stretch",
                            )
                        except Exception:
                            st.button("📷 Export PNG (kaleido required)", disabled=True, width="stretch")

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

    # ---- Cluster Triage Panel ----
    if aoi and aoi.has_data:
        with st.expander("🔬 Cluster Triage", expanded=False):
            import plotly.express as _px2
            import numpy as _np_ct

            _ct_df = aoi.all_defects.copy()
            if 'ALIGNED_X' not in _ct_df.columns or 'ALIGNED_Y' not in _ct_df.columns:
                st.info("Load AOI data with TGZ design to enable cluster triage.")
            else:
                _ct_xy = _ct_df[['ALIGNED_X', 'ALIGNED_Y']].dropna()
                if len(_ct_xy) < 5:
                    st.info("Not enough defects for cluster analysis.")
                else:
                    try:
                        from sklearn.cluster import DBSCAN as _DBSCAN
                        _labels = _DBSCAN(eps=1.5, min_samples=3).fit_predict(_ct_xy.values)
                        _ct_df = _ct_df.loc[_ct_xy.index].copy()
                        _ct_df['_cluster'] = _labels

                        # Build cluster summary with severity score
                        _rows = []
                        for _cid in sorted(set(_labels)):
                            if _cid == -1:
                                continue
                            _cl = _ct_df[_ct_df['_cluster'] == _cid]
                            _cnt = len(_cl)
                            _bu_spread = _cl['BUILDUP'].nunique() if 'BUILDUP' in _cl.columns else 1
                            _cx = round(float(_cl['ALIGNED_X'].mean()), 2)
                            _cy = round(float(_cl['ALIGNED_Y'].mean()), 2)
                            _top_type = _cl['DEFECT_TYPE'].value_counts().idxmax() if 'DEFECT_TYPE' in _cl.columns else '—'
                            _top_pct  = _cl['DEFECT_TYPE'].value_counts().iloc[0] / _cnt * 100
                            # Severity: count × buildup spread (multi-layer clusters are more critical)
                            _severity = round(_cnt * (1 + 0.5 * (_bu_spread - 1)), 1)
                            _rows.append({
                                'Cluster': _cid,
                                'Defects': _cnt,
                                'Severity ▼': _severity,
                                'Layers': _bu_spread,
                                'Top Type': f"{_top_type} ({_top_pct:.0f}%)",
                                'Centroid X': _cx,
                                'Centroid Y': _cy,
                            })

                        if not _rows:
                            st.info("No clusters found with current thresholds.")
                        else:
                            _ct_summary = pd.DataFrame(_rows).sort_values('Severity ▼', ascending=False)
                            _noise = int((_labels == -1).sum())
                            _n_cl  = len(_rows)

                            _cs1, _cs2, _cs3 = st.columns(3)
                            _cs1.metric("Clusters Found", _n_cl)
                            _cs2.metric("Clustered Defects", int((_labels != -1).sum()))
                            _cs3.metric("Isolated (noise)", _noise)

                            # Top callout
                            _top = _ct_summary.iloc[0]
                            if _top['Layers'] > 1:
                                st.error(
                                    f"⚠️ **Critical**: Cluster {int(_top['Cluster'])} spans "
                                    f"**{int(_top['Layers'])} buildup layers** — potential registration or "
                                    f"process-wide defect. Severity score: {_top['Severity ▼']}"
                                )
                            else:
                                st.warning(
                                    f"Highest severity cluster: **{int(_top['Defects'])} defects** "
                                    f"at ({_top['Centroid X']}, {_top['Centroid Y']}) mm — "
                                    f"{_top['Top Type']}. Severity: {_top['Severity ▼']}"
                                )

                            # Ranked table
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
                                },
                            )

                            _tp1, _tp2 = st.columns(2)

                            # Bar: severity per cluster
                            with _tp1:
                                _sev_fig = _px2.bar(
                                    _ct_summary, x='Cluster', y='Severity ▼',
                                    color='Severity ▼', color_continuous_scale='OrRd',
                                    title='Cluster Severity',
                                )
                                _sev_fig.update_layout(
                                    plot_bgcolor='#000000', paper_bgcolor='#000000',
                                    font=dict(color='#cccccc'), showlegend=False,
                                    coloraxis_showscale=False,
                                    margin=dict(l=0, r=0, t=36, b=0), height=280,
                                )
                                st.plotly_chart(_sev_fig, width='stretch')

                            # Scatter: cluster positions on unit
                            with _tp2:
                                _sc_fig = _px2.scatter(
                                    _ct_summary, x='Centroid X', y='Centroid Y',
                                    size='Defects', color='Severity ▼',
                                    color_continuous_scale='OrRd',
                                    title='Cluster Positions',
                                    hover_data=['Cluster', 'Top Type', 'Layers'],
                                )
                                _sc_fig.update_layout(
                                    plot_bgcolor='#000000', paper_bgcolor='#000000',
                                    font=dict(color='#cccccc'),
                                    coloraxis_showscale=False,
                                    margin=dict(l=0, r=0, t=36, b=0), height=280,
                                )
                                st.plotly_chart(_sc_fig, width='stretch')

                    except ImportError:
                        st.warning("sklearn required for cluster triage: pip install scikit-learn")

    # ---- Job Registration & Trend Analysis ----
    if aoi and aoi.has_data:
        with st.expander("📈 Job Registry & Trend Analysis", expanded=False):
            from job_registry import register_job, list_jobs, get_job_density_summary
            import hashlib as _hl

            reg_col1, reg_col2, reg_col3, reg_col4 = st.columns([3, 3, 2, 2])
            job_id = reg_col1.text_input("Job ID", value="", key="reg_job_id", placeholder="e.g. LOT-2026-0327")
            panel_id = reg_col2.text_input("Panel ID", value="", key="reg_panel_id", placeholder="e.g. Panel-01")
            date_val = reg_col3.date_input("Date", key="reg_date")
            if reg_col4.button("Register Job", width="stretch") and job_id:
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
                             width="stretch", hide_index=True)

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
                    st.plotly_chart(trend_fig, width="stretch")

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
                        st.plotly_chart(heatmap_fig, width="stretch")

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
