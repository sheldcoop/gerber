"""
scoring.py — Defect priority scoring for VRS stepper navigation.

Computes a priority score per defect based on:
- Defect type severity (Critical > High > Medium > Low)
- Cluster density (defects near other defects are higher priority)
- Buildup layer weight (inner layers harder to rework)

The VRS stepper navigates defects in descending priority order so operators
address the most impactful defects first.
"""

from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Severity weights (shared with visualizer.py severity map)
# ---------------------------------------------------------------------------

SEVERITY_KEYWORDS: dict[int, list[str]] = {
    3: ['short', 'open', 'missing', 'bridge', 'break'],
    2: ['space', 'island', 'cut', 'excess', 'pinhole', 'void'],
    1: ['nick', 'deformation', 'scratch', 'dent', 'mark'],
    0: ['minimum', 'protrusion', 'roughness', 'residue', 'discolor'],
}

SEVERITY_BASE_SCORES: dict[int, float] = {
    3: 100.0,  # Critical
    2: 50.0,   # High
    1: 20.0,   # Medium
    0: 5.0,    # Low
}


def classify_severity(defect_type: str) -> int:
    """Map a defect type string to severity level 0-3."""
    dt_lower = str(defect_type).lower()
    for severity, keywords in SEVERITY_KEYWORDS.items():
        if any(kw in dt_lower for kw in keywords):
            return severity
    return 1  # default: medium


def score_defect_priority(df: pd.DataFrame) -> pd.Series:
    """Compute priority score for each defect in the DataFrame.

    Args:
        df: DataFrame with columns ALIGNED_X, ALIGNED_Y, DEFECT_TYPE,
            and optionally BUILDUP.

    Returns:
        pd.Series of float scores (higher = more urgent), same index as df.
    """
    if df.empty:
        return pd.Series(dtype=float)

    n = len(df)
    scores = np.zeros(n, dtype=np.float64)

    # 1. Base score from defect type severity
    if 'DEFECT_TYPE' in df.columns:
        severity_levels = df['DEFECT_TYPE'].apply(classify_severity).values
        for i, sev in enumerate(severity_levels):
            scores[i] += SEVERITY_BASE_SCORES.get(sev, 20.0)

    # 2. Cluster density bonus (neighbors within 2mm radius)
    if 'ALIGNED_X' in df.columns and 'ALIGNED_Y' in df.columns and n > 1:
        coords = df[['ALIGNED_X', 'ALIGNED_Y']].values
        try:
            from scipy.spatial import KDTree
            tree = KDTree(coords)
            # Count neighbors within 2mm for each point
            neighbor_counts = tree.query_ball_point(coords, r=2.0, return_length=True)
            # Subtract 1 (self) and cap bonus at 50
            density_bonus = np.minimum(50.0, (neighbor_counts - 1) * 5.0)
            scores += density_bonus
        except ImportError:
            # scipy not available; skip density bonus
            pass

    # 3. Buildup layer weight (higher buildup = inner layers = harder to rework)
    if 'BUILDUP' in df.columns:
        buildup_vals = pd.to_numeric(df['BUILDUP'], errors='coerce').fillna(0).values
        scores += buildup_vals * 10.0

    return pd.Series(scores, index=df.index, name='PRIORITY_SCORE')


def build_severity_map(defect_types) -> dict[str, str]:
    """Map defect types to severity labels. Shared utility for scoring + visualization."""
    severity_labels = ['Low', 'Medium', 'High', 'Critical']
    return {dt: severity_labels[classify_severity(dt)] for dt in defect_types}
