"""Tests for core/svg_utils.py — SVG stacking/rotation helpers."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.svg_utils import build_stack_svg, _SVG_BG

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
