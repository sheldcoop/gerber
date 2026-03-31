"""
gerber_renderer.py — ODB++ to Gerbonara CAM-quality SVG renderer.

Public API:
  render_odb_to_cam(data, filename, layer_filter) → RenderedODB
  scan_available_layers(data) → [(name, type), ...]
  render_layer_svg(data, layer_name, fg_color, bg_color) → SVG string

Implementation is split across core/ modules:
  core/cache.py         — disk cache + SVG data URL helpers
  core/panel_builder.py — composite panel SVG from unit SVGs
  core/layer_renderer.py — parse ODB++ layer into GerberFile
  core/step_layout.py   — step-repeat hierarchy → unit positions
  core/pipeline.py      — main rendering pipeline
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from gerbonara import GerberFile

from odb_parser import (
    _extract_odb_tgz,
    _parse_matrix,
    _scan_layers_dir,
    _find_step,
)

from core.cache import save_render_cache, load_render_cache  # re-exported for callers
from core.pipeline import _render_pipeline, LAYER_COLORS
from core.panel_builder import build_panel_svg  # re-exported for views that import it here


# Pre-defined color palette for stacking (also used by pipeline.py)
# Kept here so external code can import LAYER_COLORS from gerber_renderer.
__all__ = [
    'RenderedLayer', 'PanelLayout', 'RenderedODB', 'LAYER_COLORS',
    'render_odb_to_cam', 'scan_available_layers', 'render_layer_svg',
]


@dataclass
class RenderedLayer:
    """A single rendered copper layer with pre-cached SVG variants."""
    name: str
    layer_type: str
    svg_string: str              # default copper color SVG
    svg_data_url: str            # pre-encoded base64 data URL (default color)
    color_svg_urls: dict         # {color_hex: data_url} for stacking
    gerber_file: GerberFile
    bounds: tuple                # (min_x, min_y, max_x, max_y) in mm
    feature_count: int
    panel_svg_data_url: str = '' # pre-rendered panel tile SVG (composite SVG data url)
    stats: dict = field(default_factory=dict)


@dataclass
class PanelLayout:
    """Panel tiling layout derived from TGZ STEP-REPEAT data."""
    unit_positions: list          # [(x_mm, y_mm), ...] centered for panel SVG display
    unit_bounds: tuple            # (width_mm, height_mm) of a single unit
    total_units: int
    rows: int                     # effective total rows across entire panel
    cols: int                     # effective total cols across entire panel
    step_hierarchy: dict          # raw step-repeat data {step: [StepRepeat, ...]}
    panel_width: float = 510.0    # mm (from ODB++ panel profile)
    panel_height: float = 515.0   # mm (from ODB++ panel profile)
    unit_positions_raw: list = field(default_factory=list)  # raw ODB++ coords (pre-centering)


@dataclass
class RenderedODB:
    """All rendered layers from an ODB++ archive."""
    layers: dict  # name → RenderedLayer
    board_bounds: tuple  # aggregate (min_x, min_y, max_x, max_y) in mm
    step_name: str = ''
    units: str = ''
    panel_layout: Optional[PanelLayout] = None
    warnings: list = field(default_factory=list)


# ── Public API ────────────────────────────────────────────────────────────────

def render_odb_to_cam(data: bytes, filename: str = '',
                      layer_filter: list = None) -> RenderedODB:
    """
    Parse ODB++ archive and render each copper layer as CAM-quality SVG.

    Args:
        data: raw bytes of the .tgz archive
        filename: original filename (for error messages)
        layer_filter: optional list of layer names to render (None = all copper)

    Returns:
        RenderedODB with SVG strings and GerberFile objects per layer.
    """
    cache_hit = load_render_cache(data)
    if cache_hit:
        return cache_hit
    result = _render_pipeline(data, filename, layer_filter)
    save_render_cache(data, result)
    return result


def scan_available_layers(data: bytes) -> list:
    """
    Quick scan of ODB++ archive — returns [(name, type), ...] for renderable layers.
    No geometry parsing, just reads the matrix file. Takes ~0.1s.
    """
    import re
    import shutil
    _IMPEDANCE_RE = re.compile(r'^L\d{2}_', re.IGNORECASE)
    renderable_types = {'copper', 'signal', 'power', 'mixed', 'soldermask', 'drill'}

    tmp_dir, job_root = _extract_odb_tgz(data)
    try:
        matrix_layers = _parse_matrix(job_root)
        if not matrix_layers:
            steps_dir = os.path.join(job_root, 'steps')
            step_name = 'unit' if os.path.isdir(os.path.join(steps_dir, 'unit')) else _find_step(job_root)
            matrix_layers = _scan_layers_dir(os.path.join(job_root, 'steps', step_name, 'layers'))
        return [
            (n, t) for n, t in matrix_layers
            if t in renderable_types and not _IMPEDANCE_RE.match(n)
        ]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def render_layer_svg(data: bytes, layer_name: str,
                     fg_color: str = '#b87333', bg_color: str = '#060A06') -> Optional[str]:
    """Convenience: render a single layer and return SVG string."""
    result = render_odb_to_cam(data, layer_filter=[layer_name])
    layer = result.layers.get(layer_name) or next(iter(result.layers.values()), None)
    if layer:
        svg_tag = layer.gerber_file.to_svg(fg=fg_color, bg=bg_color)
        return str(svg_tag)
    return None
