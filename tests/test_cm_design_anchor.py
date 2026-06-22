"""Tests for views/cm_render._design_anchor — how the reference design overlay is
anchored + sized in the Unit Commonality view.

Two regimes:
  1. Unit frame (a real board profile → cell bigger than copper bbox): anchor the
     design at its native unit-local coordinates (shift 0) and use the unit cell as
     the reference footprint, so copper sits where it physically is and registers
     with the defect cloud (which uses the same step-origin frame).
  2. Legacy fallback (no profile → cell ≈ copper bbox): shift the copper bbox corner
     to the origin, exactly as before.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from views.cm_render import _design_anchor


# copper bbox offset inside the unit (min corner at (2, 3))
_COPPER = (2.0, 3.0, 22.0, 33.0)  # copper_w = 20, copper_h = 30


class TestUnitFrame:
    def test_native_anchor_when_unit_larger(self):
        # Profile gives a unit clearly larger than the copper bbox.
        shift, ref_w, ref_h = _design_anchor(_COPPER, cell_w=24.0, cell_h=34.0)
        assert shift == (0.0, 0.0)          # no corner shift — keep native coords
        assert (ref_w, ref_h) == (24.0, 34.0)  # reference footprint == unit cell

    def test_native_anchor_when_one_dim_larger(self):
        # Only width exceeds copper; still treated as a unit frame.
        shift, ref_w, ref_h = _design_anchor(_COPPER, cell_w=25.0, cell_h=30.0)
        assert shift == (0.0, 0.0)
        assert (ref_w, ref_h) == (25.0, 30.0)


class TestLegacyFallback:
    def test_corner_shift_when_cell_equals_copper(self):
        # No usable profile → cell == copper bbox size → legacy behaviour.
        shift, ref_w, ref_h = _design_anchor(_COPPER, cell_w=20.0, cell_h=30.0)
        assert shift == (-2.0, -3.0)        # copper bbox corner → origin
        assert (ref_w, ref_h) == (20.0, 30.0)

    def test_corner_shift_within_tolerance(self):
        # Sub-tolerance difference still counts as the copper-bbox fallback.
        shift, _, _ = _design_anchor(_COPPER, cell_w=20.2, cell_h=30.2)
        assert shift == (-2.0, -3.0)
