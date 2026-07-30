import numpy as np
import pandas as pd
from alignment import calculate_geometry, INTER_UNIT_GAP


def panels_per_cell_grid(df, panel_col: str, n_rows: int, n_cols: int):
    """Grid of distinct-panel counts per unit cell.

    ``grid[row, col]`` = number of distinct ``panel_col`` values among defects whose
    ``(UNIT_INDEX_Y, UNIT_INDEX_X) == (row, col)``. Cells with no defects are 0.

    This is the vectorized replacement for the panel-heatmap's former per-cell
    boolean-mask loop, which allocated a full-DataFrame mask and called ``.nunique()``
    once per cell — O(cells × defects). A single groupby is O(defects). Returns an
    all-zero grid when the required columns are absent (matching the old fallback).
    """
    grid = np.zeros((n_rows, n_cols), dtype=float)
    if (panel_col not in df.columns
            or 'UNIT_INDEX_X' not in df.columns
            or 'UNIT_INDEX_Y' not in df.columns):
        return grid
    nunq = df.groupby(['UNIT_INDEX_Y', 'UNIT_INDEX_X'])[panel_col].nunique()
    for (ri, ci), val in nunq.items():
        ri, ci = int(ri), int(ci)
        if 0 <= ri < n_rows and 0 <= ci < n_cols:
            grid[ri, ci] = val
    return grid

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
    panel_filter: tuple | None = None,
    verif_filter: tuple | None = None,
) -> pd.DataFrame:
    """Scope-filter AOI defects — the single chokepoint for every view.

    ``buildup_filter`` keeps its legacy semantics: an empty tuple means "keep all"
    (the global scope bar guarantees at least one buildup is selected anyway).

    ``panel_filter`` and ``verif_filter`` distinguish "not filtering" from "nothing
    selected": ``None`` skips the filter entirely, while an empty tuple filters to an
    empty frame. That distinction matters because clearing the verification
    multiselect must show *nothing*, not silently fall back to everything.
    """
    src = _df.copy()
    if buildup_filter and 'BUILDUP' in src.columns:
        src = src[src['BUILDUP'].isin(buildup_filter)]
    if 'SIDE' in src.columns:
        if 'Front' in side_filter and 'Back' not in side_filter:
            src = src[src['SIDE'] == 'F']
        elif 'Back' in side_filter and 'Front' not in side_filter:
            src = src[src['SIDE'] == 'B']
    if panel_filter is not None and 'PANEL_ID' in src.columns:
        src = src[src['PANEL_ID'].isin(panel_filter)]
    if verif_filter is not None and 'VERIFICATION' in src.columns:
        src = src[src['VERIFICATION'].isin(verif_filter)]
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

