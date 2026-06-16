"""alignment_geometry.py — panel geometry constants + cell/origin geometry."""

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# Panel geometry constants (fixed for all jobs)
FRAME_WIDTH:    float = 510.0
FRAME_HEIGHT:   float = 515.0
FIXED_GAP_X:    float = 3.0
FIXED_GAP_Y:    float = 3.0
INTER_UNIT_GAP: float = 0.25


@dataclass
class GeometryContext:
    """Fully resolved physical dimensions for the panel layout.

    Mirrors faster-aoi's GeometryContext. All distances in mm.
    Y increases upward (mathematical convention, same as AOI machine output).
    Quadrant labels: Q1=top-left, Q2=bottom-left, Q3=bottom-right, Q4=top-right.
    """
    # Active panel (excluding frame margins and gaps)
    panel_width:   float
    panel_height:  float
    # Each quadrant
    quad_width:    float
    quad_height:   float
    # Individual unit cell
    cell_width:    float
    cell_height:   float
    # Stride = cell + inter_unit_gap
    stride_x:      float
    stride_y:      float
    # Full inter-quadrant gap (fixed + 2*dyn)
    effective_gap_x: float
    effective_gap_y: float
    # Absolute start position of the first unit (Q2 origin) from frame corner
    offset_x:      float
    offset_y:      float
    # Absolute origins of each quadrant (bottom-left corner of quadrant in mm)
    # Q2=bottom-left, Q3=bottom-right, Q1=top-left, Q4=top-right
    quadrant_origins: dict




def calculate_geometry(
    panel_rows: int,
    panel_cols: int,
    dyn_gap_x: float,
    dyn_gap_y: float,
    frame_width: float = FRAME_WIDTH,
    frame_height: float = FRAME_HEIGHT,
    fixed_offset_x: float = 13.5,
    fixed_offset_y: float = 15.0,
    fixed_gap_x: float = FIXED_GAP_X,
    fixed_gap_y: float = FIXED_GAP_Y,
    inter_unit_gap: float = INTER_UNIT_GAP,
) -> GeometryContext:
    """Single source of truth for all physical panel layout maths.

    Exact port of faster-aoi GeometryEngine.calculate_layout().

    Args:
        panel_rows: Number of unit rows per quadrant (user-given).
        panel_cols: Number of unit columns per quadrant (user-given).
        dyn_gap_x:  User-given dynamic inter-quadrant gap in X (mm).
        dyn_gap_y:  User-given dynamic inter-quadrant gap in Y (mm).
        frame_width:  Total PCB frame width in mm (fixed: 510).
        frame_height: Total PCB frame height in mm (fixed: 515).
        fixed_offset_x: Fixed frame margin in X (fixed: 13.5 mm).
        fixed_offset_y: Fixed frame margin in Y (fixed: 15.0 mm).
        fixed_gap_x: Fixed structural quad gap in X (fixed: 3.0 mm).
        fixed_gap_y: Fixed structural quad gap in Y (fixed: 3.0 mm).
        inter_unit_gap: Gap between individual units (fixed: 0.25 mm).

    Returns:
        GeometryContext with all derived dimensions.
    """
    # 1. Active panel dimensions (mirrors faster-aoi exactly)
    p_width  = frame_width  - 2 * fixed_offset_x - fixed_gap_x - 4 * dyn_gap_x
    p_height = frame_height - 2 * fixed_offset_y - fixed_gap_y - 4 * dyn_gap_y

    quad_width  = p_width  / 2
    quad_height = p_height / 2

    # 2. Inter-quadrant gaps (fixed + 2×dynamic, one side each)
    effective_gap_x = fixed_gap_x + 2 * dyn_gap_x
    effective_gap_y = fixed_gap_y + 2 * dyn_gap_y

    # 3. Absolute start position (Q2 structural origin = bottom-left active area)
    total_off_x = fixed_offset_x + dyn_gap_x
    total_off_y = fixed_offset_y + dyn_gap_y

    # 4. Unit cell dimensions
    cell_width  = (quad_width  - (panel_cols + 1) * inter_unit_gap) / panel_cols
    cell_height = (quad_height - (panel_rows + 1) * inter_unit_gap) / panel_rows

    stride_x = cell_width  + inter_unit_gap
    stride_y = cell_height + inter_unit_gap

    # 5. Quadrant origins (bottom-left corner of each quadrant)
    # Y-up convention: larger Y = higher on panel = Q1 / Q4 row
    q_bottom_left  = (total_off_x,                         total_off_y)                        # Q2
    q_bottom_right = (total_off_x + quad_width + effective_gap_x, total_off_y)                 # Q3
    q_top_left     = (total_off_x,                         total_off_y + quad_height + effective_gap_y)  # Q1
    q_top_right    = (total_off_x + quad_width + effective_gap_x, total_off_y + quad_height + effective_gap_y)  # Q4

    origins = {
        'Q2': q_bottom_left,
        'Q3': q_bottom_right,
        'Q1': q_top_left,
        'Q4': q_top_right,
    }

    return GeometryContext(
        panel_width=p_width, panel_height=p_height,
        quad_width=quad_width, quad_height=quad_height,
        cell_width=cell_width, cell_height=cell_height,
        stride_x=stride_x, stride_y=stride_y,
        effective_gap_x=effective_gap_x, effective_gap_y=effective_gap_y,
        offset_x=total_off_x, offset_y=total_off_y,
        quadrant_origins=origins,
    )


# ---------------------------------------------------------------------------
# Bounding box overlap calculation
# ---------------------------------------------------------------------------



def calculate_physical_unit_origin(
    row: int,
    col: int,
    panel_rows_per_quad: int = 6,
    panel_cols_per_quad: int = 6,
    dyn_gap_x: float = 5.0,
    dyn_gap_y: float = 3.5,
    # Legacy kwargs kept for backward compat but ignored (constants now embedded)
    frame_width: float = FRAME_WIDTH,
    frame_height: float = FRAME_HEIGHT,
    fixed_offset_x: float = 13.5,
    fixed_offset_y: float = 15.0,
    fixed_gap_x: float = FIXED_GAP_X,
    fixed_gap_y: float = FIXED_GAP_Y,
    inter_unit_gap: float = INTER_UNIT_GAP,
) -> tuple[float, float]:
    """Compute absolute physical X, Y (mm) for the origin of a specific unit.

    Delegates to calculate_geometry() — single source of truth.

    Args:
        row: Global unit row index (0-based, continuous across quads).
        col: Global unit column index (0-based, continuous across quads).
        panel_rows_per_quad: Rows per quadrant (user-given).
        panel_cols_per_quad: Columns per quadrant (user-given).
        dyn_gap_x: User-given dynamic inter-quadrant gap X (mm).
        dyn_gap_y: User-given dynamic inter-quadrant gap Y (mm).

    Returns:
        (x_mm, y_mm) absolute position from frame corner.
    """
    ctx = calculate_geometry(
        panel_rows=panel_rows_per_quad,
        panel_cols=panel_cols_per_quad,
        dyn_gap_x=dyn_gap_x,
        dyn_gap_y=dyn_gap_y,
        frame_width=frame_width,
        frame_height=frame_height,
        fixed_offset_x=fixed_offset_x,
        fixed_offset_y=fixed_offset_y,
        fixed_gap_x=fixed_gap_x,
        fixed_gap_y=fixed_gap_y,
        inter_unit_gap=inter_unit_gap,
    )

    quad_col = col // panel_cols_per_quad
    quad_row = row // panel_rows_per_quad
    local_col = col % panel_cols_per_quad
    local_row = row % panel_rows_per_quad

    # Quadrant origin (bottom-left of that quadrant)
    quad_x = ctx.offset_x + quad_col * (ctx.quad_width  + ctx.effective_gap_x)
    quad_y = ctx.offset_y + quad_row * (ctx.quad_height + ctx.effective_gap_y)

    unit_x = quad_x + inter_unit_gap + local_col * ctx.stride_x
    unit_y = quad_y + inter_unit_gap + local_row * ctx.stride_y

    return unit_x, unit_y




def get_panel_quadrant_bounds(
    panel_rows_per_quad: int = 6,
    panel_cols_per_quad: int = 6,
    dyn_gap_x: float = 5.0,
    dyn_gap_y: float = 3.5,
    frame_width: float = FRAME_WIDTH,
    frame_height: float = FRAME_HEIGHT,
    fixed_offset_x: float = 13.5,
    fixed_offset_y: float = 15.0,
    fixed_gap_x: float = FIXED_GAP_X,
    fixed_gap_y: float = FIXED_GAP_Y,
) -> dict[str, tuple[float, float, float, float]]:
    """Compute absolute bounding boxes (x0, y0, x1, y1) for the frame and 4 quadrants.

    Q label convention (Y increases upward, AOI/math convention):
      Q1 = top-left,  Q2 = bottom-left,
      Q3 = bottom-right, Q4 = top-right.

    Args:
        panel_rows_per_quad: Rows per quadrant.
        panel_cols_per_quad: Columns per quadrant.
        dyn_gap_x: User-given dynamic gap X (mm).
        dyn_gap_y: User-given dynamic gap Y (mm).

    Returns:
        Dict with keys 'frame', 'Q1', 'Q2', 'Q3', 'Q4', each (x0, y0, x1, y1).
    """
    ctx = calculate_geometry(
        panel_rows=panel_rows_per_quad,
        panel_cols=panel_cols_per_quad,
        dyn_gap_x=dyn_gap_x,
        dyn_gap_y=dyn_gap_y,
        frame_width=frame_width,
        frame_height=frame_height,
        fixed_offset_x=fixed_offset_x,
        fixed_offset_y=fixed_offset_y,
        fixed_gap_x=fixed_gap_x,
        fixed_gap_y=fixed_gap_y,
    )

    qw, qh = ctx.quad_width, ctx.quad_height
    bounds: dict[str, tuple[float, float, float, float]] = {}
    for label, (ox, oy) in ctx.quadrant_origins.items():
        bounds[label] = (ox, oy, ox + qw, oy + qh)

    bounds['frame'] = (0.0, 0.0, frame_width, frame_height)
    return bounds

