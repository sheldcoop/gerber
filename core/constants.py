"""
core/constants.py — single source of truth for shared rendering & domain constants.

This is a LEAF module: it must not import anything from the rest of the project, so any
module can depend on it without creating a cycle. Producer modules (svg_utils, pipeline,
cache, panel_builder, gerber_renderer, visualizer, the views) import their constants from
here instead of redefining them, which removes the "change one hex, forget the other five"
class of bugs.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Core render colours (baked into every layer SVG by the renderer)
# ---------------------------------------------------------------------------
COPPER_FG = '#b87333'   # default copper foreground
DRILL_FG = '#FFD700'    # drill / via foreground (gold)
SVG_BG = '#060A06'      # opaque panel background colour


def layer_fg(layer_type: str) -> str:
    """Natural foreground colour for a layer type."""
    return DRILL_FG if layer_type == 'drill' else COPPER_FG


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

# Distinct hues for telling stacked layers apart (shared by the views + sidebar swatches).
LAYER_PALETTE = [
    '#FF9800',  # amber
    '#2196F3',  # blue
    '#4CAF50',  # green
    '#9C27B0',  # purple
    '#00BCD4',  # cyan
    '#E91E63',  # pink
    '#CDDC39',  # lime
    '#F44336',  # red
]

# Per-layer stacking colours assigned at render time (pipeline).
STACK_LAYER_COLORS = ['#b87333', '#4488cc', '#44aa44', '#9966bb', '#cc6644',
                      '#44ccaa', '#cc4466', '#44cccc']

# Categorical palette for defect types / verification codes.
DEFECT_TYPE_COLORS = [
    '#FF4444', '#44FF44', '#4444FF', '#FFAA00', '#FF44FF',
    '#44FFFF', '#FF8844', '#88FF44', '#4488FF', '#FF4488',
    '#AAFF44', '#44AAFF', '#FF44AA', '#44FFAA', '#AA44FF',
    '#FFFF44', '#FF6644', '#66FF44', '#4466FF', '#FF4466',
]

# Engineering-standard RGB colours per PCB layer type (used by the rasteriser).
LAYER_TYPE_COLORS = {
    'copper':      {'r': 184, 'g': 115, 'b': 51},   # copper brown
    'soldermask':  {'r': 0,   'g': 128, 'b': 0},     # PCB green
    'silkscreen':  {'r': 255, 'g': 255, 'b': 255},   # white
    'paste':       {'r': 192, 'g': 192, 'b': 192},   # silver
    'outline':     {'r': 255, 'g': 215, 'b': 0},     # gold
    'drill':       {'r': 100, 'g': 100, 'b': 100},   # dark grey
    'other':       {'r': 100, 'g': 100, 'b': 200},   # muted blue
}

# ---------------------------------------------------------------------------
# Marker styles for defect scatter overlays
# ---------------------------------------------------------------------------
MARKER_STYLES = {
    'dot':       {'symbol': 'circle', 'size': 8,  'line': {'width': 1, 'color': 'black'}},
    'crosshair': {'symbol': 'cross',  'size': 12, 'line': {'width': 2, 'color': 'black'}},
    'x_mark':    {'symbol': 'x',      'size': 10, 'line': {'width': 2, 'color': 'black'}},
}

# ---------------------------------------------------------------------------
# Layer classification & stacking order
# ---------------------------------------------------------------------------

# Layer types treated as copper for colouring / ordering / outline rendering.
COPPER_TYPES = ('copper', 'signal', 'power', 'mixed')

# Layer types the pipeline will actually render.
RENDERABLE_TYPES = {'copper', 'signal', 'power', 'mixed', 'soldermask', 'drill'}

# Draw-order bands: larger = drawn later = nearer the top of the stack.
LAYER_Z = {
    'drill': 0, 'other': 1, 'paste': 2,
    'soldermask': 3, 'silkscreen': 4, 'outline': 5, 'copper': 6,
}

# Physical copper stackup, BOTTOM-of-stack first → TOP last (front outer on top).
COPPER_STACK_ORDER = ['4B', '3B', '2B', '1BCO', '1FCO', '2F', '3F', '4F']

# Same stackup TOP→BOTTOM (front outer first) — the order copper is listed in the sidebar
# and the index used to assign each copper layer a unique palette colour.
COPPER_TOP_DOWN = list(reversed(COPPER_STACK_ORDER))  # 4F,3F,2F,1FCO,1BCO,2B,3B,4B


def copper_order_index(name: str):
    """Top→bottom stackup index for a copper layer (4F=0 … 4B=7), else None.

    Single source for: sidebar list order, draw order, and distinct colour assignment.
    """
    n = (name or '').upper()
    for i, key in enumerate(COPPER_TOP_DOWN):
        if n == key or n.startswith(key):
            return i
    return None

# ---------------------------------------------------------------------------
# Opacity defaults for the design overlay
# ---------------------------------------------------------------------------
DEFAULT_LAYER_OPACITY = 0.85
LAYER_OPACITY_SINGLE = {'copper': 0.95, 'drill': 0.55, 'other': 0.60}
LAYER_OPACITY_MULTI = {'copper': 0.90, 'drill': 0.45, 'other': 0.50}

# ---------------------------------------------------------------------------
# Cache location
# ---------------------------------------------------------------------------
CAM_CACHE_DIR = Path.home() / '.cache' / 'gerber-vrs' / 'cam'
