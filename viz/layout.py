from __future__ import annotations
"""viz/layout.py — overlay configuration + Plotly layout helpers."""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import plotly.graph_objects as go

from core.constants import (
    LAYER_TYPE_COLORS as LAYER_COLORS,
    DEFECT_TYPE_COLORS,
    MARKER_STYLES,
)


@dataclass
class OverlayConfig:
    """Configuration for the overlay visualization."""
    visible_layers: list[str] = field(default_factory=list)
    layer_opacities: dict[str, float] = field(default_factory=dict)
    defect_types: list[str] = field(default_factory=list)
    buildup_filter: list[int] = field(default_factory=list)
    side_filter: str = 'Both'        # 'Front', 'Back', 'Both'
    marker_style: str = 'crosshair'  # 'crosshair', 'dot', 'x_mark'
    color_mode: str = 'by_type'      # 'by_type', 'by_verification'
    board_bounds: tuple[float, float, float, float] = (0, 0, 0, 0)
    offset_x: float = 0.0            # Visual X translation for the ODB++ render
    offset_y: float = 0.0            # Visual Y translation for the ODB++ render
    active_defect_x: float | None = None  # X coordinate for VRS targeting
    active_defect_y: float | None = None  # Y coordinate for VRS targeting
    crop_bounds: tuple[float, float, float, float] | None = None # Explicit viewport bounds for Geometry culling
    min_feature_size: float | None = None  # LOD: skip features narrower than this (mm)



def _apply_layout(fig: go.Figure, config: OverlayConfig) -> None:
    """
    Apply Plotly layout settings: pure black theme, locked aspect ratio,
    zoom/pan controls, no axes, no grid.
    """
    bounds = config.board_bounds

    _AXIS_CLEAN = dict(
        title='',
        showgrid=False,
        zeroline=False,
        showticklabels=False,
        showline=False,
        ticks='',
    )

    fig.update_layout(
        plot_bgcolor='#000000',
        paper_bgcolor='#000000',
        font=dict(color='#cccccc', size=12),

        xaxis={**_AXIS_CLEAN, 'range': [bounds[0], bounds[2]]},
        yaxis={**_AXIS_CLEAN,
               'range': [bounds[1], bounds[3]],
               'scaleanchor': 'x',
               'scaleratio': 1},

        legend=dict(
            bgcolor='rgba(0,0,0,0.85)',
            bordercolor='rgba(0,200,80,0.30)',
            borderwidth=1,
            font=dict(size=11, color='#cccccc'),
            itemclick='toggle',
            itemdoubleclick='toggleothers',
            x=1.02,
            y=1.0,
            xanchor='left',
            yanchor='top',
        ),
        showlegend=True,

        dragmode='pan',
        hovermode='closest',
        margin=dict(l=0, r=160, t=36, b=0),
        height=800,
    )


def _smart_tick(axis_range: float) -> Optional[float]:
    """Compute a sensible tick interval based on axis range."""
    if axis_range <= 0:
        return None
    # Target ~10-20 ticks
    raw = axis_range / 15
    # Round to nearest power of 10, 2, or 5
    magnitude = 10 ** int(np.floor(np.log10(raw)))
    for multiplier in [1, 2, 5, 10]:
        if magnitude * multiplier >= raw:
            return magnitude * multiplier
    return magnitude * 10


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


