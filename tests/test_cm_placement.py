"""Tests for views/cm_render._layer_placement — the rotation-aware layer anchor math.

Locks two guarantees:
  1. The reference (full-board) layer's placement is IDENTICAL to the previous swap formula
     at 0/90/270 — so copper can never silently drift.
  2. A sub-region layer (e.g. a drill via cloud inset from the board) maps a given board
     point to the SAME screen coords as the full-board copper — i.e. drill aligns with copper
     under rotation (the bug this fixes).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from views.cm_render import _layer_placement


def _old_formula(b, ref_shift, swap, svg_rot):
    """Re-implementation of the PRE-FIX placement, to pin copper behaviour."""
    im_w, im_h = b[2] - b[0], b[3] - b[1]
    sx, sy = ref_shift
    layer_cx = b[0] + sx + im_w / 2.0
    layer_cy = b[3] + sy - im_h / 2.0
    if swap:
        final_cx, final_cy = im_h / 2.0, im_w / 2.0
    else:
        final_cx, final_cy = layer_cx, layer_cy
    svg_is_swapped = round(svg_rot) % 360 in (90, 270)
    szx, szy = (im_h, im_w) if svg_is_swapped else (im_w, im_h)
    return final_cx - szx / 2.0, final_cy + szy / 2.0, szx, szy


# Reference cell: a board with these bounds is the "full-board" reference layer.
_BOARD = (-21.45, -18.45, 21.45, 18.45)
_W = _BOARD[2] - _BOARD[0]
_H = _BOARD[3] - _BOARD[1]
_SHIFT = (-_BOARD[0], -_BOARD[1])


class TestReferenceUnchanged:
    """Full-board layer placement must equal the old formula at every angle."""

    def test_matches_old_formula(self):
        for ang in (0, 90, 270):
            swap = ang in (90, 270)
            new = _layer_placement(_BOARD, _SHIFT, _W, _H, ang, ang)
            old = _old_formula(_BOARD, _SHIFT, swap, ang)
            for a, b in zip(new, old):
                assert abs(a - b) < 1e-9, f"angle {ang}: {new} != {old}"


class TestSubRegionAlignsWithCopper:
    """A via at board point P must land at the same screen coords as the copper pad at P."""

    # Drill via cloud occupies a sub-region inset from the board edges.
    _DRILL = (-18.0, -15.0, 19.0, 16.0)

    def _screen_of_point(self, layer_bounds, px, py, ang):
        """Where board point (px,py) lands on screen, given the layer it's drawn in.

        The image maps its viewBox (== layer bounds) onto [x, x+szx] × [y-szy, y]; a point
        at fraction f across the bounds maps to the same fraction across the placed image.
        """
        x, y, szx, szy = _layer_placement(layer_bounds, _SHIFT, _W, _H, ang, ang)
        b = layer_bounds
        # viewBox spans bounds; after 90/270 content rotation the axes swap. Model the
        # mapping for each orthogonal angle explicitly (content rotates with the panel).
        fx = (px - b[0]) / (b[2] - b[0])
        fy = (py - b[1]) / (b[3] - b[1])
        if ang == 0:
            sx_ = x + fx * szx
            sy_ = y - (1 - fy) * szy
        elif ang == 90:
            sx_ = x + (1 - fy) * szx
            sy_ = y - (1 - fx) * szy
        else:  # 270
            sx_ = x + fy * szx
            sy_ = y - fx * szy
        return sx_, sy_

    def test_via_lands_on_copper_pad(self):
        # Pick a few board points that exist in both copper (full board) and the drill cloud.
        for px, py in [(0.0, 0.0), (10.0, -8.0), (-15.0, 12.0), (18.0, 15.0)]:
            for ang in (0, 90, 270):
                cu = self._screen_of_point(_BOARD, px, py, ang)
                dr = self._screen_of_point(self._DRILL, px, py, ang)
                assert abs(cu[0] - dr[0]) < 1e-6 and abs(cu[1] - dr[1]) < 1e-6, \
                    f"point {(px, py)} angle {ang}: copper {cu} != drill {dr}"
