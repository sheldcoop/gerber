"""
export.py — Export pipeline for defect overlay views and per-unit CSV reports.

Provides:
- High-resolution image export (PNG/SVG) of the current Plotly view
- Per-unit defect summary CSV
"""

from typing import Optional

import pandas as pd
import plotly.graph_objects as go


def export_current_view(fig: go.Figure, fmt: str = 'png', scale: int = 3) -> bytes:
    """Export the current Plotly figure as an image.

    Args:
        fig: Plotly Figure to export.
        fmt: Image format — 'png', 'svg', 'pdf', or 'jpeg'.
        scale: Resolution multiplier (default 3x for high-res).

    Returns:
        Image bytes.
    """
    return fig.to_image(format=fmt, scale=scale, engine='kaleido')


def export_unit_csv(df: pd.DataFrame) -> str:
    """Generate a per-unit defect summary CSV string.

    Columns: unit_x, unit_y, defect_type, buildup, verified_count, unverified_count

    Args:
        df: Aligned defect DataFrame with UNIT_INDEX_X, UNIT_INDEX_Y,
            DEFECT_TYPE, and optionally BUILDUP, VERIFICATION columns.

    Returns:
        CSV string ready for download.
    """
    if df.empty:
        return "unit_x,unit_y,defect_type,buildup,verified_count,unverified_count\n"

    # Determine groupby columns
    group_cols = []
    if 'UNIT_INDEX_X' in df.columns:
        group_cols.append('UNIT_INDEX_X')
    if 'UNIT_INDEX_Y' in df.columns:
        group_cols.append('UNIT_INDEX_Y')
    if 'DEFECT_TYPE' in df.columns:
        group_cols.append('DEFECT_TYPE')
    if 'BUILDUP' in df.columns:
        group_cols.append('BUILDUP')

    if not group_cols:
        return "unit_x,unit_y,defect_type,buildup,verified_count,unverified_count\n"

    has_verif = 'VERIFICATION' in df.columns

    rows = []
    for keys, group in df.groupby(group_cols, observed=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        key_dict = dict(zip(group_cols, keys))

        verified = 0
        unverified = len(group)
        if has_verif:
            verified = int((group['VERIFICATION'].str.upper() != 'F').sum())
            unverified = len(group) - verified

        rows.append({
            'unit_x': key_dict.get('UNIT_INDEX_X', ''),
            'unit_y': key_dict.get('UNIT_INDEX_Y', ''),
            'defect_type': key_dict.get('DEFECT_TYPE', ''),
            'buildup': key_dict.get('BUILDUP', ''),
            'verified_count': verified,
            'unverified_count': unverified,
        })

    result_df = pd.DataFrame(rows)
    return result_df.to_csv(index=False)
