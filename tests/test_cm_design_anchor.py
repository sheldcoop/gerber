"""Tests for views/cm_render._design_anchor — how the reference design overlay is
anchored + sized in the Unit Commonality view.

The copper bbox (true size) is centered inside the unit cell [0, cell]. Defects are
placed as X_MM − step_origin and fill the same cell from the lower-left corner, so
centering copper makes the two register with an equal margin on every side. When there
is no board profile (cell == copper bbox) the shift reduces to the legacy corner shift
(−copper_min).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from views.cm_render import _design_anchor


# copper bbox offset inside the unit (min corner at (2, 3)), copper_w=20, copper_h=30
_COPPER = (2.0, 3.0, 22.0, 33.0)


class TestUnitFrame:
    def test_centers_copper_when_unit_larger(self):
        # Unit clearly larger than the copper bbox → copper centered, footprint == cell.
        shift, ref_w, ref_h = _design_anchor(_COPPER, cell_w=24.0, cell_h=34.0)
        # shift = ((24-20)/2 - 2, (34-30)/2 - 3) = (0, -1)
        assert shift == (0.0, -1.0)
        assert (ref_w, ref_h) == (24.0, 34.0)

    def test_centers_copper_one_dim_larger(self):
        shift, ref_w, ref_h = _design_anchor(_COPPER, cell_w=25.0, cell_h=30.0)
        # shift = ((25-20)/2 - 2, (30-30)/2 - 3) = (0.5, -3)
        assert shift == (0.5, -3.0)
        assert (ref_w, ref_h) == (25.0, 30.0)

    def test_equal_margin_on_both_sides(self):
        # After applying the shift, copper's left and right margins inside the cell match.
        cell_w, cell_h = 25.0, 35.0
        (sx, sy), _, _ = _design_anchor(_COPPER, cell_w, cell_h)
        cmnx, cmny, cmxx, cmxy = _COPPER
        left_margin = cmnx + sx                 # copper min mapped into the cell
        right_margin = cell_w - (cmxx + sx)      # gap from copper max to cell edge
        assert abs(left_margin - right_margin) < 1e-9
        bottom_margin = cmny + sy
        top_margin = cell_h - (cmxy + sy)
        assert abs(bottom_margin - top_margin) < 1e-9


class TestLegacyFallback:
    def test_corner_shift_when_cell_equals_copper(self):
        # No usable profile → cell == copper bbox size → reduces to legacy corner shift.
        shift, ref_w, ref_h = _design_anchor(_COPPER, cell_w=20.0, cell_h=30.0)
        assert shift == (-2.0, -3.0)        # copper bbox corner → origin
        assert (ref_w, ref_h) == (20.0, 30.0)
