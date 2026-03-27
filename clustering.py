"""
clustering.py — Defect cluster intelligence via DBSCAN.

Identifies spatial clusters of defects on the panel, labels them,
and provides ranked cluster summaries for operator triage.
"""

from typing import Optional

import numpy as np
import pandas as pd


def compute_clusters(
    df: pd.DataFrame,
    eps: float = 2.0,
    min_samples: int = 3,
) -> pd.DataFrame:
    """Run DBSCAN clustering on aligned defect coordinates.

    Args:
        df: DataFrame with ALIGNED_X, ALIGNED_Y columns.
        eps: Maximum distance (mm) between two samples in the same cluster.
        min_samples: Minimum cluster size.

    Returns:
        Copy of df with added CLUSTER_ID column (-1 = noise/unclustered).
    """
    df = df.copy()
    df['CLUSTER_ID'] = -1

    if df.empty or 'ALIGNED_X' not in df.columns or 'ALIGNED_Y' not in df.columns:
        return df

    coords = df[['ALIGNED_X', 'ALIGNED_Y']].values
    if len(coords) < min_samples:
        return df

    try:
        from sklearn.cluster import DBSCAN
        clustering = DBSCAN(eps=eps, min_samples=min_samples, metric='euclidean')
        labels = clustering.fit_predict(coords)
        df['CLUSTER_ID'] = labels
    except ImportError:
        # scikit-learn not installed; fall back to grid-based approximation
        df = _grid_cluster_fallback(df, cell_size=eps, min_count=min_samples)

    return df


def _grid_cluster_fallback(
    df: pd.DataFrame,
    cell_size: float = 2.0,
    min_count: int = 3,
) -> pd.DataFrame:
    """Simple grid-based clustering when scikit-learn is unavailable.

    Bins defects into grid cells and assigns cluster IDs to cells
    meeting the minimum count threshold.
    """
    df = df.copy()
    df['_gx'] = (df['ALIGNED_X'] / cell_size).astype(int)
    df['_gy'] = (df['ALIGNED_Y'] / cell_size).astype(int)

    cell_counts = df.groupby(['_gx', '_gy']).size().reset_index(name='_count')
    valid_cells = cell_counts[cell_counts['_count'] >= min_count].copy()
    valid_cells['CLUSTER_ID'] = range(len(valid_cells))

    df = df.merge(valid_cells[['_gx', '_gy', 'CLUSTER_ID']], on=['_gx', '_gy'], how='left')
    df['CLUSTER_ID'] = df['CLUSTER_ID'].fillna(-1).astype(int)
    df.drop(columns=['_gx', '_gy'], inplace=True)
    return df


def get_cluster_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Generate ranked cluster summary for triage.

    Args:
        df: DataFrame with CLUSTER_ID, ALIGNED_X, ALIGNED_Y, DEFECT_TYPE,
            and optionally BUILDUP, SIDE columns.

    Returns:
        DataFrame with: cluster_id, defect_count, centroid_x, centroid_y,
        dominant_type, dominant_pct, buildup_info. Sorted by defect_count desc.
    """
    clustered = df[df['CLUSTER_ID'] >= 0]
    if clustered.empty:
        return pd.DataFrame(columns=[
            'cluster_id', 'defect_count', 'centroid_x', 'centroid_y',
            'dominant_type', 'dominant_pct', 'buildup_info',
        ])

    rows = []
    for cid, group in clustered.groupby('CLUSTER_ID'):
        n = len(group)
        cx = group['ALIGNED_X'].mean()
        cy = group['ALIGNED_Y'].mean()

        # Dominant defect type
        if 'DEFECT_TYPE' in group.columns:
            type_counts = group['DEFECT_TYPE'].value_counts()
            dominant_type = type_counts.index[0]
            dominant_pct = type_counts.iloc[0] / n * 100
        else:
            dominant_type = 'Unknown'
            dominant_pct = 100.0

        # Buildup info
        buildup_info = ''
        if 'BUILDUP' in group.columns and 'SIDE' in group.columns:
            bu_side = group.groupby(['BUILDUP', 'SIDE']).size().reset_index(name='cnt')
            parts = [f"BU-{int(r.BUILDUP):02d} {'Front' if r.SIDE == 'F' else 'Back'}" for _, r in bu_side.iterrows()]
            buildup_info = ', '.join(parts)

        rows.append({
            'cluster_id': cid,
            'defect_count': n,
            'centroid_x': round(cx, 2),
            'centroid_y': round(cy, 2),
            'dominant_type': dominant_type,
            'dominant_pct': round(dominant_pct, 1),
            'buildup_info': buildup_info,
        })

    summary = pd.DataFrame(rows)
    return summary.sort_values('defect_count', ascending=False).reset_index(drop=True)


def get_cluster_hull_coords(df: pd.DataFrame, cluster_id: int) -> Optional[tuple[list, list]]:
    """Get convex hull coordinates for a specific cluster for Plotly rendering.

    Returns:
        (xs, ys) coordinate lists for the hull boundary, or None if < 3 points.
    """
    cluster_pts = df[df['CLUSTER_ID'] == cluster_id]
    if len(cluster_pts) < 3:
        return None

    try:
        from scipy.spatial import ConvexHull
        coords = cluster_pts[['ALIGNED_X', 'ALIGNED_Y']].values
        hull = ConvexHull(coords)
        hull_pts = coords[hull.vertices]
        # Close the polygon
        xs = list(hull_pts[:, 0]) + [hull_pts[0, 0]]
        ys = list(hull_pts[:, 1]) + [hull_pts[0, 1]]
        return xs, ys
    except Exception:
        return None
