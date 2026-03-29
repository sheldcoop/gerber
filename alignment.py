"""
alignment.py — Coordinate alignment between Gerber and AOI defect data.

Two alignment methods:
1. Simple offset (default): Both coordinate systems are in mm with board-edge
   origin. After unit conversion (AOI microns → mm), compute bounding-box
   center offset to align the two datasets.

2. Fiducial-based affine transform: When fiducial marker coordinates are
   available in both the Gerber data and AOI data, compute a least-squares
   affine transformation (translation + rotation + scale) for precise alignment.

The alignment result includes overlap metrics and debug information to help
diagnose misalignment issues.

Panel geometry mirrors the faster-aoi GeometryEngine exactly:
  - Fixed frame: 510 x 515 mm
  - Fixed margins: 13.5 mm X, 15.0 mm Y (inlined defaults, no named constants)
  - Fixed structural gap: 3.0 mm
  - User-given dynamic gap: dyn_gap_x / dyn_gap_y
  - Always 2×2 quadrant grid (Q1=top-left, Q2=bottom-left, Q3=bottom-right, Q4=top-right)
  - Unit indices: 0-based, continuous across both quads (0 → 2*N-1)
"""

import hashlib
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# Panel geometry constants (fixed for all jobs)
# ---------------------------------------------------------------------------

FRAME_WIDTH:      float = 510.0
FRAME_HEIGHT:     float = 515.0
FIXED_GAP_X:      float = 3.0
FIXED_GAP_Y:      float = 3.0
INTER_UNIT_GAP:   float = 0.25



# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AlignmentResult:
    """Result of coordinate alignment computation."""
    method: str = 'unit_conversion'  # 'unit_conversion', 'offset', 'affine'
    offset_x: float = 0.0           # X translation in mm
    offset_y: float = 0.0           # Y translation in mm
    scale_x: float = 1.0            # X scale factor
    scale_y: float = 1.0            # Y scale factor
    rotation_deg: float = 0.0       # Rotation in degrees
    overlap_pct: float = 0.0        # Bounding box overlap percentage
    transform_matrix: Optional[np.ndarray] = None  # 3x3 affine matrix
    gerber_bounds: tuple[float, float, float, float] = (0, 0, 0, 0)
    aoi_bounds: tuple[float, float, float, float] = (0, 0, 0, 0)
    origin_x: float = 0.0
    origin_y: float = 0.0
    flip_y: bool = False
    confidence: float = 0.0         # 0.0-1.0, alignment quality metric
    fiducials_used: int = 0         # Number of fiducial pairs used
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Panel Geometry Engine (port of faster-aoi GeometryEngine)
# ---------------------------------------------------------------------------

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

def _compute_overlap(
    bounds_a: tuple[float, float, float, float],
    bounds_b: tuple[float, float, float, float],
) -> float:
    """
    Compute the overlap percentage between two axis-aligned bounding boxes.

    Returns a value between 0 (no overlap) and 100 (perfect overlap).
    Uses intersection area / smaller area as the metric, so if one dataset
    is fully contained within the other, overlap = 100%.
    """
    # Intersection rectangle
    ix_min = max(bounds_a[0], bounds_b[0])
    iy_min = max(bounds_a[1], bounds_b[1])
    ix_max = min(bounds_a[2], bounds_b[2])
    iy_max = min(bounds_a[3], bounds_b[3])

    if ix_min >= ix_max or iy_min >= iy_max:
        return 0.0

    intersection_area = (ix_max - ix_min) * (iy_max - iy_min)

    # Use the smaller bounding box area as denominator
    area_a = (bounds_a[2] - bounds_a[0]) * (bounds_a[3] - bounds_a[1])
    area_b = (bounds_b[2] - bounds_b[0]) * (bounds_b[3] - bounds_b[1])
    smaller_area = min(area_a, area_b)

    if smaller_area <= 0:
        return 0.0

    return min(100.0, (intersection_area / smaller_area) * 100.0)


# ---------------------------------------------------------------------------
# Simple offset alignment
# ---------------------------------------------------------------------------

def _align_simple(
    gerber_bounds: tuple[float, float, float, float],
    aoi_bounds: tuple[float, float, float, float],
) -> AlignmentResult:
    """
    Simple alignment by matching bounding box origins (lower-left).

    Both Gerber and AOI use board-edge as origin. After micron→mm conversion,
    coordinates should be directly comparable. We compute the offset between
    the two bounding box lower-left corners.

    If Gerber origin is at (0,0) — typical for RS274X — and AOI is also
    relative to board edge, the offset should be minimal or zero.
    """
    # Gerber lower-left
    g_x0, g_y0 = gerber_bounds[0], gerber_bounds[1]
    # AOI lower-left
    a_x0, a_y0 = aoi_bounds[0], aoi_bounds[1]

    # Offset = Gerber origin - AOI origin
    # Adding this offset to AOI coords maps them into Gerber space
    offset_x = g_x0 - a_x0
    offset_y = g_y0 - a_y0

    # Compute overlap after applying offset
    shifted_aoi = (
        aoi_bounds[0] + offset_x,
        aoi_bounds[1] + offset_y,
        aoi_bounds[2] + offset_x,
        aoi_bounds[3] + offset_y,
    )
    overlap = _compute_overlap(gerber_bounds, shifted_aoi)

    warnings = []
    if overlap < 50:
        warnings.append(
            f"Low coordinate overlap ({overlap:.1f}%). "
            "Gerber and AOI extents may not be aligned. "
            "Check that both use the same board-edge origin, "
            "or provide fiducial data for affine alignment."
        )

    # Build identity-based affine with translation
    matrix = np.eye(3)
    matrix[0, 2] = offset_x
    matrix[1, 2] = offset_y

    return AlignmentResult(
        method='offset',
        offset_x=offset_x,
        offset_y=offset_y,
        overlap_pct=overlap,
        transform_matrix=matrix,
        gerber_bounds=gerber_bounds,
        aoi_bounds=aoi_bounds,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Fiducial-based affine alignment
# ---------------------------------------------------------------------------

def _align_affine(
    fiducials_gerber: list[tuple[float, float]],
    fiducials_aoi: list[tuple[float, float]],
    gerber_bounds: tuple[float, float, float, float],
    aoi_bounds: tuple[float, float, float, float],
) -> AlignmentResult:
    """
    Compute affine transformation from AOI coordinates to Gerber coordinates
    using matched fiducial point pairs.

    Requires at least 3 fiducial pairs for a full affine (translation +
    rotation + scale + shear). With exactly 2 pairs, computes similarity
    transform (translation + rotation + uniform scale).

    The affine matrix M maps AOI coords to Gerber coords:
        [x_gerber]     [x_aoi]
        [y_gerber] = M [y_aoi]
        [   1   ]     [  1   ]
    """
    n = len(fiducials_gerber)
    if n < 2:
        return AlignmentResult(
            method='affine',
            gerber_bounds=gerber_bounds,
            aoi_bounds=aoi_bounds,
            warnings=["Need at least 2 fiducial pairs for affine alignment"]
        )

    src = np.array(fiducials_aoi, dtype=np.float64)    # source (AOI)
    dst = np.array(fiducials_gerber, dtype=np.float64)  # destination (Gerber)

    if n == 2:
        # Similarity transform (4 DOF: tx, ty, scale, rotation)
        # Solve: dst = scale * R * src + t
        dx_src = src[1, 0] - src[0, 0]
        dy_src = src[1, 1] - src[0, 1]
        dx_dst = dst[1, 0] - dst[0, 0]
        dy_dst = dst[1, 1] - dst[0, 1]

        len_src = math.sqrt(dx_src**2 + dy_src**2)
        len_dst = math.sqrt(dx_dst**2 + dy_dst**2)

        if len_src < 1e-9:
            return AlignmentResult(
                method='affine', gerber_bounds=gerber_bounds,
                aoi_bounds=aoi_bounds,
                warnings=["Fiducial points are too close together in AOI data"]
            )

        scale = len_dst / len_src
        angle_src = math.atan2(dy_src, dx_src)
        angle_dst = math.atan2(dy_dst, dx_dst)
        rotation = angle_dst - angle_src

        cos_r = math.cos(rotation) * scale
        sin_r = math.sin(rotation) * scale

        # Translation: dst[0] = M * src[0] + t
        tx = dst[0, 0] - (cos_r * src[0, 0] - sin_r * src[0, 1])
        ty = dst[0, 1] - (sin_r * src[0, 0] + cos_r * src[0, 1])

        matrix = np.array([
            [cos_r, -sin_r, tx],
            [sin_r,  cos_r, ty],
            [0,      0,     1 ],
        ])

        rotation_deg = math.degrees(rotation)

    else:
        # Full affine (6 DOF) via least squares
        # For each point: x' = a*x + b*y + tx, y' = c*x + d*y + ty
        # Build system: A * params = b
        A = np.zeros((2 * n, 6))
        b = np.zeros(2 * n)

        for i in range(n):
            sx, sy = src[i]
            dx_val, dy_val = dst[i]
            A[2*i]     = [sx, sy, 1, 0,  0,  0]
            A[2*i + 1] = [0,  0,  0, sx, sy, 1]
            b[2*i]     = dx_val
            b[2*i + 1] = dy_val

        # Least squares solution
        params, residuals, rank, sv = np.linalg.lstsq(A, b, rcond=None)
        a, b_val, tx, c, d, ty = params

        matrix = np.array([
            [a,     b_val, tx],
            [c,     d,     ty],
            [0,     0,     1 ],
        ])

        # Extract rotation and scale from the matrix
        scale_x = math.sqrt(a**2 + c**2)
        scale_y = math.sqrt(b_val**2 + d**2)
        scale = (scale_x + scale_y) / 2
        rotation_deg = math.degrees(math.atan2(c, a))

    # Compute residual error for each fiducial pair
    residuals = []
    for i in range(n):
        pt_aoi = np.array([src[i, 0], src[i, 1], 1.0])
        pt_mapped = matrix @ pt_aoi
        err = math.sqrt((pt_mapped[0] - dst[i, 0])**2 + (pt_mapped[1] - dst[i, 1])**2)
        residuals.append(err)

    max_residual = max(residuals)
    avg_residual = sum(residuals) / len(residuals)

    warnings = []
    if max_residual > 0.1:  # > 100 microns
        warnings.append(
            f"High fiducial residual error: max={max_residual:.3f}mm, avg={avg_residual:.3f}mm. "
            "Check fiducial point matching."
        )

    # Compute overlap after transformation
    # Transform AOI bounding box corners
    corners_aoi = np.array([
        [aoi_bounds[0], aoi_bounds[1], 1],
        [aoi_bounds[2], aoi_bounds[1], 1],
        [aoi_bounds[2], aoi_bounds[3], 1],
        [aoi_bounds[0], aoi_bounds[3], 1],
    ])
    transformed = (matrix @ corners_aoi.T).T
    tx_min = transformed[:, 0].min()
    ty_min = transformed[:, 1].min()
    tx_max = transformed[:, 0].max()
    ty_max = transformed[:, 1].max()
    transformed_aoi_bounds = (tx_min, ty_min, tx_max, ty_max)

    overlap = _compute_overlap(gerber_bounds, transformed_aoi_bounds)

    return AlignmentResult(
        method='affine',
        offset_x=matrix[0, 2],
        offset_y=matrix[1, 2],
        scale_x=scale if n == 2 else scale_x,
        scale_y=scale if n == 2 else scale_y,
        rotation_deg=rotation_deg,
        overlap_pct=overlap,
        transform_matrix=matrix,
        gerber_bounds=gerber_bounds,
        aoi_bounds=aoi_bounds,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Column names that indicate fiducial data is present
FIDUCIAL_COLUMNS = {
    'FIDUCIAL_X', 'FIDUCIAL_Y',
    'FID_X', 'FID_Y',
    'REF_X', 'REF_Y',
    'FIDUCIAL', 'FID'
}


def detect_fiducials(df: pd.DataFrame) -> Optional[list[tuple[float, float]]]:
    """
    Check if the AOI DataFrame contains fiducial reference columns.

    Returns a list of (x_mm, y_mm) fiducial points, or None if not found.
    """
    cols_upper = {c.upper(): c for c in df.columns}

    fid_x_col = None
    fid_y_col = None
    for candidate in ['FIDUCIAL_X', 'FID_X', 'REF_X']:
        if candidate in cols_upper:
            fid_x_col = cols_upper[candidate]
            break
    for candidate in ['FIDUCIAL_Y', 'FID_Y', 'REF_Y']:
        if candidate in cols_upper:
            fid_y_col = cols_upper[candidate]
            break

    # Some machines export a unified 'FIDUCIAL' column or 'FID' as string maybe
    # But usually it's X and Y.
    if fid_x_col is None or fid_y_col is None:
        return None

    # Extract unique fiducial coordinates
    fid_df = df[[fid_x_col, fid_y_col]].dropna().drop_duplicates()
    if fid_df.empty:
        return None

    points = []
    for _, row in fid_df.iterrows():
        x = float(row[fid_x_col])
        y = float(row[fid_y_col])
        # Convert microns to mm if values seem to be in microns (> 1000)
        if abs(x) > 1000 or abs(y) > 1000:
            x /= 1000.0
            y /= 1000.0
        points.append((x, y))

    return points if len(points) >= 2 else None


def _pair_fiducials(
    gerber_fids: list[tuple[float, float]],
    aoi_fids: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]], list[str]]:
    """Pair fiducial points by sorting both sets by angle from centroid.

    Returns matched (gerber, aoi) lists of equal length plus any warnings.
    """
    warnings: list[str] = []
    n_g, n_a = len(gerber_fids), len(aoi_fids)
    if n_g != n_a:
        warnings.append(
            f"Fiducial count mismatch: {n_g} Gerber vs {n_a} AOI. "
            f"Using the first {min(n_g, n_a)} pairs sorted by angle."
        )

    def _sort_by_angle(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not pts:
            return pts
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        return sorted(pts, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))

    sorted_g = _sort_by_angle(gerber_fids)
    sorted_a = _sort_by_angle(aoi_fids)
    n = min(len(sorted_g), len(sorted_a))
    return sorted_g[:n], sorted_a[:n], warnings


def _compute_confidence(
    n_pairs: int,
    max_residual: float,
    overlap_pct: float,
) -> float:
    """Compute alignment confidence from fiducial metrics (0.0–1.0)."""
    pair_factor = min(1.0, n_pairs / 3.0)
    residual_factor = max(0.0, 1.0 - max_residual / 0.5)
    overlap_factor = min(1.0, overlap_pct / 100.0)
    return pair_factor * residual_factor * overlap_factor


def compute_alignment(
    gerber_bounds: tuple[float, float, float, float],
    aoi_bounds: tuple[float, float, float, float],
    aoi_df: Optional[pd.DataFrame] = None,
    fiducials_gerber: Optional[list[tuple[float, float]]] = None,
    fiducials_aoi: Optional[list[tuple[float, float]]] = None,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    flip_y: bool = False,
    manual_offset_x: float = 0.0,
    manual_offset_y: float = 0.0,
) -> AlignmentResult:
    """
    Compute coordinate alignment between Gerber and AOI coordinate spaces.

    Alignment method priority:
    1. Fiducial-based affine (when both gerber + aoi fiducials available)
    2. Auto-detected fiducials from AOI DataFrame (when gerber fiducials exist)
    3. Simple bounding-box offset (fallback)

    Every fallback is surfaced as a user-visible warning — no silent downgrades.
    """
    g_width = gerber_bounds[2] - gerber_bounds[0]
    g_height = gerber_bounds[3] - gerber_bounds[1]
    a_width = aoi_bounds[2] - aoi_bounds[0]
    a_height = aoi_bounds[3] - aoi_bounds[1]

    warnings: list[str] = []
    if g_width <= 0 or g_height <= 0:
        warnings.append("Gerber bounding box has zero area — no geometry parsed")
    if a_width <= 0 or a_height <= 0:
        warnings.append("AOI bounding box has zero area — no valid coordinates")

    # --- Path 1: Explicit fiducial pairs provided ---
    if fiducials_gerber and fiducials_aoi:
        paired_g, paired_a, pair_warns = _pair_fiducials(fiducials_gerber, fiducials_aoi)
        warnings.extend(pair_warns)
        if len(paired_g) >= 2:
            result = _align_affine(paired_g, paired_a, gerber_bounds, aoi_bounds)
            result.fiducials_used = len(paired_g)
            # Compute residual for confidence
            max_res = 0.0
            if result.transform_matrix is not None:
                for i in range(len(paired_a)):
                    pt = result.transform_matrix @ [paired_a[i][0], paired_a[i][1], 1]
                    err = math.sqrt((pt[0] - paired_g[i][0])**2 + (pt[1] - paired_g[i][1])**2)
                    max_res = max(max_res, err)
            result.confidence = _compute_confidence(len(paired_g), max_res, result.overlap_pct)
            result.warnings = warnings + result.warnings
            result.origin_x = origin_x
            result.origin_y = origin_y
            result.flip_y = flip_y
            return result

    # --- Path 2: Auto-detect AOI fiducials from DataFrame ---
    if aoi_df is not None and fiducials_gerber:
        aoi_fiducials = detect_fiducials(aoi_df)
        if aoi_fiducials:
            paired_g, paired_a, pair_warns = _pair_fiducials(fiducials_gerber, aoi_fiducials)
            warnings.extend(pair_warns)
            if len(paired_g) >= 2:
                result = _align_affine(paired_g, paired_a, gerber_bounds, aoi_bounds)
                result.fiducials_used = len(paired_g)
                max_res = 0.0
                if result.transform_matrix is not None:
                    for i in range(len(paired_a)):
                        pt = result.transform_matrix @ [paired_a[i][0], paired_a[i][1], 1]
                        err = math.sqrt((pt[0] - paired_g[i][0])**2 + (pt[1] - paired_g[i][1])**2)
                        max_res = max(max_res, err)
                result.confidence = _compute_confidence(len(paired_g), max_res, result.overlap_pct)
                if result.confidence < 0.3:
                    warnings.append(
                        f"Low fiducial alignment confidence ({result.confidence:.2f}). "
                        "Auto-detected AOI fiducials may not match Gerber fiducials correctly. "
                        "Verify alignment visually or provide explicit fiducial pairs."
                    )
                result.warnings = warnings + result.warnings
                result.origin_x = origin_x
                result.origin_y = origin_y
                result.flip_y = flip_y
                return result
            else:
                warnings.append(
                    "Fiducial columns detected in AOI data but fewer than 2 usable pairs. "
                    "Falling back to bounding-box offset alignment."
                )
    elif aoi_df is not None:
        aoi_fiducials = detect_fiducials(aoi_df)
        if aoi_fiducials:
            warnings.append(
                "Fiducial columns detected in AOI data, but no matching Gerber "
                "fiducials available from ODB++ archive. Falling back to bounding-box "
                "offset alignment. For fiducial-based alignment, ensure the ODB++ "
                "archive contains features with .fiducial attributes."
            )

    # --- Path 3: Simple offset alignment (fallback) ---
    result = _align_simple(gerber_bounds, aoi_bounds)
    result.offset_x += manual_offset_x
    result.offset_y += manual_offset_y
    result.confidence = min(1.0, result.overlap_pct / 100.0) * 0.5  # capped at 0.5 for offset
    result.fiducials_used = 0
    result.warnings = warnings + result.warnings
    result.origin_x = origin_x
    result.origin_y = origin_y
    result.flip_y = flip_y
    return result


def _alignment_result_to_hashable(result: AlignmentResult) -> tuple:
    """Convert AlignmentResult to a hashable tuple for caching."""
    matrix_tuple = tuple(map(tuple, result.transform_matrix.tolist())) if result.transform_matrix is not None else None
    return (
        result.method, result.offset_x, result.offset_y,
        result.scale_x, result.scale_y, result.rotation_deg,
        result.overlap_pct, matrix_tuple,
        result.gerber_bounds, result.aoi_bounds,
        result.origin_x, result.origin_y, result.flip_y,
        result.confidence, result.fiducials_used,
        tuple(result.warnings),
    )


@st.cache_data(show_spinner="Computing alignment...")
def compute_alignment_cached(
    gerber_bounds: tuple,
    aoi_bounds: tuple,
    aoi_data_hash: str,
    fiducials_gerber: Optional[tuple] = None,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    flip_y: bool = False,
    manual_offset_x: float = 0.0,
    manual_offset_y: float = 0.0,
    _aoi_df: Optional[pd.DataFrame] = None,
) -> dict:
    """Cached wrapper for compute_alignment. Returns dict for serialization safety."""
    fids_g = list(fiducials_gerber) if fiducials_gerber else None
    result = compute_alignment(
        gerber_bounds=gerber_bounds,
        aoi_bounds=aoi_bounds,
        aoi_df=_aoi_df,
        fiducials_gerber=fids_g,
        origin_x=origin_x,
        origin_y=origin_y,
        flip_y=flip_y,
        manual_offset_x=manual_offset_x,
        manual_offset_y=manual_offset_y,
    )
    # Serialize to dict for cache-safe storage
    return {
        'method': result.method,
        'offset_x': result.offset_x,
        'offset_y': result.offset_y,
        'scale_x': result.scale_x,
        'scale_y': result.scale_y,
        'rotation_deg': result.rotation_deg,
        'overlap_pct': result.overlap_pct,
        'transform_matrix': result.transform_matrix.tolist() if result.transform_matrix is not None else None,
        'gerber_bounds': result.gerber_bounds,
        'aoi_bounds': result.aoi_bounds,
        'origin_x': result.origin_x,
        'origin_y': result.origin_y,
        'flip_y': result.flip_y,
        'confidence': result.confidence,
        'fiducials_used': result.fiducials_used,
        'warnings': result.warnings,
    }


def _dict_to_alignment_result(d: dict) -> AlignmentResult:
    """Reconstruct AlignmentResult from cached dict."""
    matrix = np.array(d['transform_matrix']) if d['transform_matrix'] is not None else None
    return AlignmentResult(
        method=d['method'],
        offset_x=d['offset_x'],
        offset_y=d['offset_y'],
        scale_x=d['scale_x'],
        scale_y=d['scale_y'],
        rotation_deg=d['rotation_deg'],
        overlap_pct=d['overlap_pct'],
        transform_matrix=matrix,
        gerber_bounds=tuple(d['gerber_bounds']),
        aoi_bounds=tuple(d['aoi_bounds']),
        origin_x=d['origin_x'],
        origin_y=d['origin_y'],
        flip_y=d['flip_y'],
        confidence=d['confidence'],
        fiducials_used=d['fiducials_used'],
        warnings=d['warnings'],
    )


@st.cache_data(show_spinner="Applying coordinate transform...")
def apply_alignment_cached(
    _df_hash: str,
    alignment_dict: dict,
    unit_row: Optional[int] = None,
    unit_col: Optional[int] = None,
    _df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Cached wrapper for apply_alignment."""
    result = _dict_to_alignment_result(alignment_dict)
    return apply_alignment(_df, result, unit_row=unit_row, unit_col=unit_col)


def compute_dataframe_hash(df: pd.DataFrame) -> str:
    """Compute a fast hash of a DataFrame for caching keys."""
    return hashlib.md5(pd.util.hash_pandas_object(df).values.tobytes()).hexdigest()


def apply_alignment(
    df: pd.DataFrame, 
    result: AlignmentResult, 
    unit_row: Optional[int] = None, 
    unit_col: Optional[int] = None
) -> pd.DataFrame:
    """
    Apply alignment transformation to AOI defect coordinates.

    Adds ALIGNED_X and ALIGNED_Y columns to the DataFrame.
    """
    df = df.copy()
    if df.empty:
        return df

    # STEP 5: Filter single unit vs panel
    if unit_row is not None and 'UNIT_INDEX_Y' in df.columns:
        df = df[df['UNIT_INDEX_Y'] == unit_row]
    if unit_col is not None and 'UNIT_INDEX_X' in df.columns:
        df = df[df['UNIT_INDEX_X'] == unit_col]

    if df.empty:
        return df

    # STEP 1: Unit conversion (ensure source is in mm)
    if 'X_MM' not in df.columns and 'X' in df.columns:
        df['X_MM'] = df['X'] / 1000.0
    if 'Y_MM' not in df.columns and 'Y' in df.columns:
        df['Y_MM'] = df['Y'] / 1000.0

    x_vals = df['X_MM'].fillna(0.0).values
    y_vals = df['Y_MM'].fillna(0.0).values

    # STEP 2: Design origin alignment
    x_vals = x_vals - result.origin_x
    y_vals = y_vals - result.origin_y

    # STEP 4: Y axis flip (do this before affine to match geometry orientation)
    if result.flip_y:
        board_height = result.gerber_bounds[3] - result.gerber_bounds[1]
        y_vals = board_height - y_vals

    if result.transform_matrix is not None:
        # STEP 3: Apply full affine transform
        coords = np.column_stack([
            x_vals,
            y_vals,
            np.ones(len(df))
        ])
        transformed = (result.transform_matrix @ coords.T).T
        df['ALIGNED_X'] = transformed[:, 0]
        df['ALIGNED_Y'] = transformed[:, 1]
    else:
        # Since ODB++ is visually shifted, we just keep the exact physical AOI coordinates
        df['ALIGNED_X'] = x_vals
        df['ALIGNED_Y'] = y_vals

    return df


def get_debug_info(result: AlignmentResult) -> dict:
    """
    Extract human-readable debug information from an AlignmentResult.

    Used by the debug expander panel in the Streamlit UI.
    """
    return {
        'method': result.method,
        'offset_x_mm': round(result.offset_x, 4),
        'offset_y_mm': round(result.offset_y, 4),
        'scale_x': round(result.scale_x, 6),
        'scale_y': round(result.scale_y, 6),
        'rotation_deg': round(result.rotation_deg, 4),
        'overlap_pct': round(result.overlap_pct, 1),
        'confidence': round(result.confidence, 4),
        'fiducials_used': result.fiducials_used,
        'gerber_bounds': {
            'min_x': round(result.gerber_bounds[0], 3),
            'min_y': round(result.gerber_bounds[1], 3),
            'max_x': round(result.gerber_bounds[2], 3),
            'max_y': round(result.gerber_bounds[3], 3),
            'width': round(result.gerber_bounds[2] - result.gerber_bounds[0], 3),
            'height': round(result.gerber_bounds[3] - result.gerber_bounds[1], 3),
        },
        'aoi_bounds': {
            'min_x': round(result.aoi_bounds[0], 3),
            'min_y': round(result.aoi_bounds[1], 3),
            'max_x': round(result.aoi_bounds[2], 3),
            'max_y': round(result.aoi_bounds[3], 3),
            'width': round(result.aoi_bounds[2] - result.aoi_bounds[0], 3),
            'height': round(result.aoi_bounds[3] - result.aoi_bounds[1], 3),
        },
        'warnings': result.warnings,
    }


# ---------------------------------------------------------------------------
# Physical Panel Layout Calculator
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

