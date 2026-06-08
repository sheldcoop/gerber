import streamlit as st
import pandas as pd
from clustering import compute_clusters, get_cluster_summary, get_cluster_hull_coords
from alignment import calculate_geometry, INTER_UNIT_GAP
from scoring import classify_severity_by_verification

def compute_clusters_cached(_df_hash: str, _df: pd.DataFrame, eps: float, min_samples: int):
    """Run DBSCAN + summary + all hull coords."""
    clustered = compute_clusters(_df, eps=eps, min_samples=min_samples)
    summary = get_cluster_summary(clustered)
    hulls = {}
    if not summary.empty:
        for _, crow in summary.iterrows():
            h = get_cluster_hull_coords(clustered, crow['cluster_id'])
            if h:
                hulls[crow['cluster_id']] = (h, crow['defect_count'])
    return clustered, summary, hulls


def compute_panel_shapes(rows: int, cols: int, gap_x: float, gap_y: float) -> list:
    """Pre-compute all unit cell shape dicts."""
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


def compute_cm_geometry(
    unit_positions: tuple,       # tuple of (x, y) — ODB++ display (panel-absolute) coords
    first_layer_bounds: tuple,   # (min_x, min_y, max_x, max_y) of CAM layer in local space
    unit_bounds: tuple = None,   # (width_mm, height_mm) from board profile — preferred when available
) -> tuple:
    """Return (origins_dict, cell_w, cell_h).

    origins_dict maps (row_index, col_index) → (origin_x, origin_y) where:
      - row_index / col_index are 0-based sorted position indices
      - origin_x/y = the unit's display position (step origin in panel space)

    To align a defect: ALIGNED = (X_MM - origin_x, Y_MM - origin_y)
    Result is in [0, cell_w] × [0, cell_h], matching the CAM SVG in Plotly.
    """
    cam_min_x, cam_min_y, cam_max_x, cam_max_y = first_layer_bounds
    if unit_bounds and unit_bounds[0] > 0 and unit_bounds[1] > 0:
        cell_w = unit_bounds[0]
        cell_h = unit_bounds[1]
    else:
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


def filter_aoi_cm(
    _df: pd.DataFrame,
    buildup_filter: tuple,
    side_filter: tuple,
) -> pd.DataFrame:
    """Scope-filter AOI defects for Commonality."""
    src = _df.copy()
    if buildup_filter and 'BUILDUP' in src.columns:
        src = src[src['BUILDUP'].isin(buildup_filter)]
    if 'SIDE' in src.columns:
        if 'Front' in side_filter and 'Back' not in side_filter:
            src = src[src['SIDE'] == 'F']
        elif 'Back' in side_filter and 'Front' not in side_filter:
            src = src[src['SIDE'] == 'B']
    return src


def _align_defects(x_mm, y_mm, ox_arr, oy_arr, off_x, off_y):
    """Map defect X_MM/Y_MM into the unit's native design frame by translation.

    AOI reports X_MM/Y_MM such that, after subtracting the unit's step origin
    (+ optional manual offset), each defect lands in the unit's native (un-rotated)
    coordinate frame in [0, cell_w] x [0, cell_h]. This holds for both un-rotated and
    rotated (cluster-level) panels — verified against fhr0010 (0°) and fhr0020 (270°),
    where pure translation fits ~99-100% of defects in-cell. Placement rotation is a
    DISPLAY concern handled separately (see views/unit_commonality._rotate_for_display);
    the defect coordinates themselves are never inverse-rotated.

    All arrays are passed as tuples so this stays hashable for st.cache_data callers.
    """
    import numpy as _np
    if not (len(x_mm) == len(y_mm) == len(ox_arr) == len(oy_arr)):
        raise ValueError(
            f"_align_defects array length mismatch: "
            f"x={len(x_mm)} y={len(y_mm)} ox={len(ox_arr)} oy={len(oy_arr)}"
        )
    ax = _np.array(x_mm) - _np.array(ox_arr) + off_x
    ay = _np.array(y_mm) - _np.array(oy_arr) + off_y
    return tuple(ax.tolist()), tuple(ay.tolist())


# Severity label + colour used by both the chart overlay and the fingerprint table
_SEV_LABEL = {3: 'Critical', 2: 'High', 1: 'Medium', 0: 'Low'}
_SEV_COLOR = {3: '#FF3B3B', 2: '#FF9900', 1: '#FFD700', 0: '#66BB6A'}
_SEV_DOT_SCALE = {3: 18, 2: 13, 1: 9, 0: 6}   # base marker size per severity


@st.cache_data(max_entries=32, ttl=3600, show_spinner=False)

def _compute_pad_fingerprint(
    ax_tuple: tuple,
    ay_tuple: tuple,
    defect_types: tuple,
    unit_keys: tuple,
    buildup_vals: tuple,
    verification_vals: tuple = (),
    verif_severity_map: tuple = (),   # tuple of (code, int) pairs — hashable for cache
    snap_mm: float = 0.5,
) -> pd.DataFrame:
    """
    Group aligned defects by fault site (snapped to snap_mm grid) and compute:
      - unit_count      : how many UNIQUE units contributed a defect at this location
      - unit_pct        : unit_count / total_unique_units  (0-1)
      - severity        : worst classify_severity() among defect types at this location
      - top_type        : most frequent defect type
      - top_verif       : most frequent verification code (e.g. CU22, SH, OP)
      - all_verif       : all unique verification codes at this site
      - buildup_list    : sorted unique buildup values
      - cx, cy          : snap-grid centre coordinates (mm)

    Returns a DataFrame sorted by (severity DESC, unit_count DESC).
    """
    import numpy as _np

    if not ax_tuple:
        return pd.DataFrame()

    ax = _np.array(ax_tuple)
    ay = _np.array(ay_tuple)

    # Convert verif_severity_map tuple back to dict
    _vsmap = dict(verif_severity_map) if verif_severity_map else {}

    # Snap to grid
    snap_x = _np.round(ax / snap_mm).astype(int)
    snap_y = _np.round(ay / snap_mm).astype(int)

    total_units = len(set(unit_keys))

    rows = []
    from collections import defaultdict, Counter
    buckets: dict = defaultdict(list)
    for i in range(len(ax)):
        key = (int(snap_x[i]), int(snap_y[i]))
        buckets[key].append(i)

    for (sx, sy), indices in buckets.items():
        cx = sx * snap_mm
        cy = sy * snap_mm
        types_here  = [defect_types[i] for i in indices]
        units_here  = set(unit_keys[i] for i in indices)

        # Per-defect verification code, aligned 1:1 with types_here (same index space).
        # Missing/None codes become '—' so the index never drifts from the defect.
        if verification_vals:
            verif_per = [
                str(verification_vals[i]) if verification_vals[i] is not None else '—'
                for i in indices
            ]
        else:
            verif_per = ['—'] * len(indices)

        # Display values use only the real (non-'—') codes.
        _real_verif = [v for v in verif_per if v != '—']
        top_v = Counter(_real_verif).most_common(1)[0][0] if _real_verif else '—'
        all_v = ', '.join(sorted(set(_real_verif))) if _real_verif else '—'

        # Severity — user map via verification code wins over keyword heuristic.
        # Each defect's own (verification, type) pair is classified.
        sev_here = max(
            classify_severity_by_verification(verif_per[j], types_here[j], _vsmap)
            for j in range(len(indices))
        )

        top_t = Counter(types_here).most_common(1)[0][0]

        if buildup_vals:
            bu_set = sorted(set(str(buildup_vals[i]) for i in indices if buildup_vals[i] is not None))
            bu_str = ', '.join(bu_set)
        else:
            bu_str = '—'

        rows.append({
            'cx': round(cx, 3),
            'cy': round(cy, 3),
            'unit_count': len(units_here),
            'unit_pct': round(len(units_here) / max(total_units, 1) * 100, 1),
            'severity': sev_here,
            'severity_label': _SEV_LABEL[sev_here],
            'top_verif': top_v,
            'all_verif': all_v,
            'top_type': top_t,
            'buildup': bu_str,
            'defect_count': len(indices),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.sort_values(['severity', 'unit_count'], ascending=[False, False], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

