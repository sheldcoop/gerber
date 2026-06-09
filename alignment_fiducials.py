"""alignment_fiducials.py — fiducial detection, pairing and confidence."""

import math
from typing import Optional

import numpy as np
import pandas as pd


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


