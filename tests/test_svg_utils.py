"""Tests for core/svg_utils.py — SVG stacking/rotation helpers."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import base64

from core.svg_utils import (
    build_stack_svg, _SVG_BG, build_layer_url, LayerStyle, stable_layer_colors,
)


class _FakeLayer:
    """Minimal stand-in for a RenderedLayer (no color_svg_urls → svg_string path)."""
    color_svg_urls = {}
    svg_data_url = ''

    def __init__(self, svg, layer_type='copper', name='2F'):
        self.svg_string = svg
        self.layer_type = layer_type
        self.name = name


def _decode(url):
    return base64.b64decode(url.split(',', 1)[1]).decode()

# A minimal layer SVG as emitted by the renderer: opaque panel background rect
# plus a copper-coloured feature.
_FG = '#b87333'
_SAMPLE_SVG = (
    f'<svg viewBox="0 0 10 10">'
    f'<rect width="10" height="10" fill="{_SVG_BG}"/>'
    f'<path fill="{_FG}" d="M0 0h5v5h-5z"/>'
    f'</svg>'
)


def test_build_stack_svg_recolours_and_strips_background():
    """Different stack colour: fg recoloured, bg made transparent."""
    out = build_stack_svg(_SAMPLE_SVG, _FG, '#4488cc')
    assert '#4488cc' in out          # foreground recoloured
    assert _FG not in out            # original fg fully replaced
    assert _SVG_BG not in out        # opaque background gone
    assert 'fill="none"' in out      # background now transparent


def test_build_stack_svg_strips_background_when_colours_match():
    """First copper layer: stack colour equals fg, but bg must still be stripped."""
    out = build_stack_svg(_SAMPLE_SVG, _FG, _FG)
    assert _SVG_BG not in out        # opaque background still removed
    assert 'fill="none"' in out
    assert _FG in out                # foreground preserved


def test_build_stack_svg_leaves_source_unchanged():
    """The original single-layer SVG keeps its opaque background."""
    build_stack_svg(_SAMPLE_SVG, _FG, '#4488cc')
    assert _SVG_BG in _SAMPLE_SVG    # source string untouched


# ── LayerStyle render-mode matrix — locks the visual modes against regressions ──

def test_layerstyle_outline_transparent():
    """Outline (not filled): coloured wireframe on a transparent background."""
    svg = _decode(build_layer_url(_FakeLayer(_SAMPLE_SVG),
                                  style=LayerStyle(layer_color='#2196F3', outline=True)))
    assert 'fill:none !important' in svg and 'stroke:#2196F3' in svg
    assert 'background-color:none' in svg or 'fill="none"' in svg


def test_layerstyle_outline_filled():
    """Outline + filled: dark wireframe on an opaque coloured field."""
    svg = _decode(build_layer_url(_FakeLayer(_SAMPLE_SVG),
                                  style=LayerStyle(layer_color='#FF9800', outline=True, filled=True)))
    assert '#FF9800' in svg            # field takes the layer colour
    assert f'stroke:{_SVG_BG}' in svg  # strokes go dark


def test_layerstyle_invert_negative():
    """Invert (non-outline): coloured field, dark features, opaque bg (no transparency)."""
    svg = _decode(build_layer_url(_FakeLayer(_SAMPLE_SVG, layer_type='soldermask'),
                                  style=LayerStyle(layer_color='#2196F3', invert=True)))
    assert '#2196F3' in svg
    assert 'none' not in svg.split('<rect', 1)[0]  # bg not stripped to none


def test_layerstyle_plain_color_transparent():
    """Plain colour (no outline/invert): foreground recoloured, bg transparent."""
    svg = _decode(build_layer_url(_FakeLayer(_SAMPLE_SVG),
                                  style=LayerStyle(layer_color='#4CAF50')))
    assert '#4CAF50' in svg and _SVG_BG not in svg


def test_stable_layer_colors_copper_all_distinct():
    """The 8 copper layers must map to 8 distinct palette colours (no collisions)."""
    copper = ['4F', '3F', '2F', '1FCO', '1BCO', '2B', '3B', '4B']
    names = copper + ['FSR', 'BSR', '2B-3B', '1FCO-2F']
    cmap = stable_layer_colors(names)
    assert len({cmap[n] for n in copper}) == len(copper)
