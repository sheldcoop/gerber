"""
visualizer.py — facade for the Plotly figure-building package.

The implementation now lives in the `viz/` package, split by responsibility:
  - viz.layout    : OverlayConfig + layout/axis helpers + shared palettes
  - viz.geometry  : Shapely→Plotly conversion + PCB layer polygon traces
  - viz.defects   : AOI defect markers, hover/customdata, drill + component traces
  - viz.figures   : top-level figure builders

This module re-exports the public + historically-imported names so existing
`from visualizer import …` call sites (and tests) keep working unchanged.
"""

from core.constants import (
    LAYER_TYPE_COLORS as LAYER_COLORS,
    DEFECT_TYPE_COLORS,
    MARKER_STYLES,
)

from viz.layout import OverlayConfig, _apply_layout, _smart_tick
from viz.geometry import (
    _rgba, _polygon_to_coords, _geometry_to_coords, _rasterize_layer,
    _add_layer_traces, _RENDER_PRIORITY, _RASTER_THRESHOLD,
)
from viz.defects import (
    _build_hover_template, _build_customdata, _add_defect_traces,
    _add_drill_hit_traces, _add_component_traces,
)
from viz.figures import build_defect_only_figure, build_heatmap_figure

__all__ = [
    'OverlayConfig', 'LAYER_COLORS', 'DEFECT_TYPE_COLORS', 'MARKER_STYLES',
    '_apply_layout', '_smart_tick',
    '_rgba', '_polygon_to_coords', '_geometry_to_coords', '_rasterize_layer',
    '_add_layer_traces',
    '_build_hover_template', '_build_customdata', '_add_defect_traces',
    '_add_drill_hit_traces', '_add_component_traces',
    'build_defect_only_figure', 'build_heatmap_figure',
]
