from __future__ import annotations
"""viz/figures.py — top-level figure builders."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from viz.layout import OverlayConfig, _apply_layout
from viz.geometry import _add_layer_traces
from viz.defects import (
    _add_defect_traces, _add_drill_hit_traces, _add_component_traces,
    _build_hover_template, _build_customdata,
)


def build_defect_only_figure(
    defect_df: pd.DataFrame,
    config: OverlayConfig,
) -> go.Figure:
    """
    Build a Plotly figure with only AOI defects (no Gerber layers).

    Useful when no Gerber archive is uploaded but AOI data is available.
    """
    fig = go.Figure()

    if defect_df is not None and not defect_df.empty:
        _add_defect_traces(fig, defect_df, config)

    _apply_layout(fig, config)
    return fig


def build_heatmap_figure(
    defect_df: pd.DataFrame,
    config: OverlayConfig,
) -> go.Figure:
    """
    Build a Plotly figure with a density heatmap of AOI defects.
    """
    fig = go.Figure()

    if defect_df is not None and not defect_df.empty and 'ALIGNED_X' in defect_df.columns:
        # Apply filters
        mask = pd.Series(True, index=defect_df.index)

        if config.defect_types:
            mask &= defect_df['DEFECT_TYPE'].isin(config.defect_types)

        if config.buildup_filter and 'BUILDUP' in defect_df.columns:
            mask &= defect_df['BUILDUP'].isin(config.buildup_filter)

        if config.side_filter != 'Both' and 'SIDE' in defect_df.columns:
            side_code = 'F' if config.side_filter == 'Front' else 'B'
            mask &= defect_df['SIDE'] == side_code

        filtered = defect_df[mask].copy()

        if not filtered.empty:
            import numpy as _np

            xs = filtered['ALIGNED_X'].values
            ys = filtered['ALIGNED_Y'].values

            # Panel extent from config bounds (or data range as fallback)
            bx0, by0, bx1, by1 = config.board_bounds
            if bx1 <= bx0 or by1 <= by0:
                bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max(), ys.max()

            # ── Layer 1: solid binned heatmap ─────────────────────────────────
            # Bin size = panel_span / 80 clamped to 2–8 mm.
            # Each cell maps 1:1 to a real mm² region — no interpolation.
            _span   = max(bx1 - bx0, by1 - by0)
            _bin_mm = max(2.0, min(8.0, _span / 80.0))

            _nx = max(4, int(round((bx1 - bx0) / _bin_mm)))
            _ny = max(4, int(round((by1 - by0) / _bin_mm)))
            _xi = _np.linspace(bx0, bx1, _nx + 1)
            _yi = _np.linspace(by0, by1, _ny + 1)

            _counts, _, _ = _np.histogram2d(xs, ys, bins=[_xi, _yi])
            _counts = _counts.T  # rows = y, cols = x

            # Mask zero bins to transparent (set to NaN so Plotly skips them)
            _counts_masked = _np.where(_counts > 0, _counts.astype(float), _np.nan)

            _bin_cs = [
                [0.0,  'rgba(100,20,0,0.55)'],
                [0.3,  'rgba(200,50,0,0.70)'],
                [0.6,  'rgba(255,120,0,0.85)'],
                [0.85, 'rgba(255,210,0,0.92)'],
                [1.0,  'rgba(255,255,255,1.0)'],
            ]
            _xc = (_xi[:-1] + _xi[1:]) / 2  # bin centres
            _yc = (_yi[:-1] + _yi[1:]) / 2

            fig.add_trace(go.Heatmap(
                x=_xc, y=_yc, z=_counts_masked,
                colorscale=_bin_cs,
                zsmooth='best',          # smooth colours only, not the data
                showscale=False,
                hovertemplate='%{z:.0f} defects<extra></extra>',
                name='Defect Density',
            ))

            # ── Layer 2: KDE smooth overlay ───────────────────────────────────
            # scipy gaussian_kde with bandwidth = 1 unit-cell width (~5% of span).
            # Renders as a semi-transparent heatmap on top of the bin layer,
            # showing the regional "heat envelope" without hiding bin detail.
            try:
                from scipy.stats import gaussian_kde as _kde

                _bw = max(0.02, min(0.12, 40.0 / _span))  # ~30–40 mm on 510 mm panel
                _kernel = _kde(_np.vstack([xs, ys]), bw_method=_bw)

                _grid_n = 100
                _gx = _np.linspace(bx0, bx1, _grid_n)
                _gy = _np.linspace(by0, by1, _grid_n)
                _GX, _GY = _np.meshgrid(_gx, _gy)
                _Z = _kernel(_np.vstack([_GX.ravel(), _GY.ravel()])).reshape(_grid_n, _grid_n)

                # Normalise to [0,1] and zero out very low density (< 2% of max)
                _Z = _Z / _Z.max()
                _Z[_Z < 0.02] = _np.nan

                _kde_cs = [
                    [0.0,  'rgba(0,0,0,0)'],
                    [0.15, 'rgba(120,10,0,0.15)'],
                    [0.4,  'rgba(200,40,0,0.30)'],
                    [0.7,  'rgba(255,100,0,0.40)'],
                    [1.0,  'rgba(255,220,50,0.50)'],
                ]
                fig.add_trace(go.Heatmap(
                    x=_gx, y=_gy, z=_Z,
                    colorscale=_kde_cs,
                    zsmooth='best',
                    showscale=False,
                    hoverinfo='skip',
                    name='KDE Envelope',
                ))
            except ImportError:
                pass   # scipy not installed — bin layer alone is still useful

    _apply_layout(fig, config)
    return fig
