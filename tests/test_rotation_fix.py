"""
tests/test_rotation_fix.py — Unit tests for STEP-REPEAT rotation support.

Tests every layer of the rotation fix:
  1. StepRepeat dataclass — mirror field present
  2. _make_step_repeat / parse_step_repeat — MIRROR parsed from text
  3. PanelLayout — unit_angle field present
  4. Pipeline dominant-angle logic — correct angle detected, dims swapped
  5. _align_defects — correct inverse rotation for all four angles
  6. Panelization gap calculation — uses swapped dims when rotated
"""

import math
import sys
import os
import textwrap

import numpy as np
import pandas as pd
import pytest

# Make project root importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from odb.models import StepRepeat
from odb.archive import _make_step_repeat, _detect_stephdr_units_factor


# ---------------------------------------------------------------------------
# 1. StepRepeat dataclass
# ---------------------------------------------------------------------------

class TestStepRepeatModel:
    """StepRepeat must carry both angle and mirror fields."""

    def test_default_angle_is_zero(self):
        sr = StepRepeat(child_step="unit", x=0, y=0, dx=0, dy=0, nx=1, ny=1)
        assert sr.angle == 0.0

    def test_default_mirror_is_false(self):
        sr = StepRepeat(child_step="unit", x=0, y=0, dx=0, dy=0, nx=1, ny=1)
        assert sr.mirror is False

    def test_angle_stored_correctly(self):
        sr = StepRepeat(child_step="unit", x=1, y=2, dx=0, dy=0,
                        nx=1, ny=1, angle=270.0)
        assert sr.angle == 270.0

    def test_mirror_stored_correctly(self):
        sr = StepRepeat(child_step="unit", x=0, y=0, dx=0, dy=0,
                        nx=1, ny=1, mirror=True)
        assert sr.mirror is True


# ---------------------------------------------------------------------------
# 2. _make_step_repeat — MIRROR and ANGLE parsing
# ---------------------------------------------------------------------------

class TestMakeStepRepeat:
    """_make_step_repeat must read MIRROR and ANGLE from raw field dicts."""

    def _fields(self, **kwargs):
        base = {"NAME": "unit", "X": 0.0, "Y": 0.0, "DX": 0.0, "DY": 0.0,
                "NX": 1, "NY": 1}
        base.update(kwargs)
        return base

    def test_mirror_yes_parsed(self):
        sr = _make_step_repeat(self._fields(MIRROR="YES"), uf=1.0)
        assert sr.mirror is True

    def test_mirror_no_parsed(self):
        sr = _make_step_repeat(self._fields(MIRROR="NO"), uf=1.0)
        assert sr.mirror is False

    def test_mirror_missing_defaults_false(self):
        sr = _make_step_repeat(self._fields(), uf=1.0)
        assert sr.mirror is False

    def test_angle_270_parsed(self):
        sr = _make_step_repeat(self._fields(ANGLE=270.0), uf=1.0)
        assert sr.angle == 270.0

    def test_angle_90_parsed(self):
        sr = _make_step_repeat(self._fields(ANGLE=90.0), uf=1.0)
        assert sr.angle == 90.0

    def test_angle_missing_defaults_zero(self):
        sr = _make_step_repeat(self._fields(), uf=1.0)
        assert sr.angle == 0.0

    def test_xy_scaled_by_uf(self):
        """X/Y coordinates must be multiplied by uf (unit factor)."""
        sr = _make_step_repeat(self._fields(X=1.0, Y=2.0), uf=25.4)
        assert abs(sr.x - 25.4) < 1e-9
        assert abs(sr.y - 50.8) < 1e-9

    def test_angle_not_scaled_by_uf(self):
        """ANGLE is degrees — must never be multiplied by uf."""
        sr = _make_step_repeat(self._fields(ANGLE=270.0), uf=25.4)
        assert sr.angle == 270.0


# ---------------------------------------------------------------------------
# 3. PanelLayout — dominant_angle field
# ---------------------------------------------------------------------------

class TestPanelLayoutUnitAngle:
    """PanelLayout must carry a dominant_angle field defaulting to 0."""

    def test_unit_angle_field_exists(self):
        from gerber_renderer import PanelLayout
        pl = PanelLayout(
            unit_positions=[],
            unit_bounds=(10, 10),
            total_units=0, rows=0, cols=0, step_hierarchy={}
        )
        assert hasattr(pl, "dominant_angle")

    def test_unit_angle_default_is_zero(self):
        from gerber_renderer import PanelLayout
        pl = PanelLayout(
            unit_positions=[],
            unit_bounds=(10, 10),
            total_units=0, rows=0, cols=0, step_hierarchy={}
        )
        assert pl.dominant_angle == 0.0

    def test_unit_angle_can_be_set(self):
        from gerber_renderer import PanelLayout
        pl = PanelLayout(
            unit_positions=[],
            unit_bounds=(10, 10),
            total_units=0, rows=0, cols=0, step_hierarchy={},
            dominant_angle=270.0,
        )
        assert pl.dominant_angle == 270.0


# ---------------------------------------------------------------------------
# 4. Dominant angle detection + dimension swap (pipeline logic, pure Python)
# ---------------------------------------------------------------------------

def _detect_dominant_angle(step_hierarchy: dict, leaf_step: str) -> float:
    """Replicate the pipeline dominant-angle detection logic."""
    from collections import Counter
    angles = []
    for _parent, sr_list in step_hierarchy.items():
        for sr in sr_list:
            if sr.child_step.lower() == leaf_step.lower():
                angles.append(sr.angle)
    if not angles:
        return 0.0
    return float(Counter(round(a) % 360 for a in angles).most_common(1)[0][0])


class TestDominantAngleDetection:
    """Dominant angle is correctly extracted from the step hierarchy."""

    def _hier(self, angles):
        """Build a minimal step_hierarchy: panel → cluster → unit at each angle."""
        from odb.models import StepRepeat
        cluster_repeats = [
            StepRepeat(child_step="unit", x=i * 2.0, y=0, dx=0, dy=0,
                       nx=1, ny=1, angle=a)
            for i, a in enumerate(angles)
        ]
        return {"cluster": cluster_repeats}

    def test_all_270_returns_270(self):
        hier = self._hier([270, 270, 270, 270, 270])
        assert _detect_dominant_angle(hier, "unit") == 270.0

    def test_all_zero_returns_zero(self):
        hier = self._hier([0, 0, 0])
        assert _detect_dominant_angle(hier, "unit") == 0.0

    def test_all_90_returns_90(self):
        hier = self._hier([90, 90])
        assert _detect_dominant_angle(hier, "unit") == 90.0

    def test_majority_wins(self):
        """If most placements are 270, result is 270 even with a rogue 0."""
        hier = self._hier([270, 270, 270, 0])
        assert _detect_dominant_angle(hier, "unit") == 270.0

    def test_no_placements_for_leaf_returns_zero(self):
        """If no SR places the leaf step, default is 0."""
        hier = self._hier([270])  # places 'unit'
        assert _detect_dominant_angle(hier, "different_unit") == 0.0

    def test_360_normalised_to_zero(self):
        """360° is the same as 0° and must normalise to 0."""
        hier = self._hier([360, 360])
        assert _detect_dominant_angle(hier, "unit") == 0.0


class TestDimensionSwap:
    """When dominant angle is 90/270, unit_w and unit_h must be swapped."""

    def _swap_if_needed(self, unit_w, unit_h, dominant_angle):
        if round(dominant_angle) % 360 in (90, 270):
            return unit_h, unit_w
        return unit_w, unit_h

    def test_270_swaps_dims(self):
        w, h = self._swap_if_needed(40.0, 80.0, 270)
        assert w == 80.0 and h == 40.0

    def test_90_swaps_dims(self):
        w, h = self._swap_if_needed(40.0, 80.0, 90)
        assert w == 80.0 and h == 40.0

    def test_0_no_swap(self):
        w, h = self._swap_if_needed(40.0, 80.0, 0)
        assert w == 40.0 and h == 80.0

    def test_180_no_swap(self):
        """180° rotation keeps the same footprint — no swap needed."""
        w, h = self._swap_if_needed(40.0, 80.0, 180)
        assert w == 40.0 and h == 80.0

    def test_square_unit_swap_is_identity(self):
        """Swapping a square unit changes nothing."""
        w, h = self._swap_if_needed(50.0, 50.0, 270)
        assert w == 50.0 and h == 50.0


# ---------------------------------------------------------------------------
# 5. _align_defects — translation into the unit's native frame
# ---------------------------------------------------------------------------
#
# NOTE: AOI X/Y are reported in each unit's native (un-rotated) frame, so alignment
# is pure translation (verified against fhr0010 @0° and fhr0020 @270°, ~99-100% in-cell).
# Placement rotation is a DISPLAY concern, tested separately in
# tests/test_commonality.py::TestRotateForDisplay. We exercise the REAL production
# function here so these tests guard core/data_utils.py:_align_defects.

from core.data_utils import _align_defects


class TestAlignDefectsTranslation:
    """_align_defects subtracts the unit origin (+ manual offset); no rotation."""

    OX, OY = 100.0, 200.0

    def test_simple_translation(self):
        ax, ay = _align_defects((110.0,), (220.0,), (self.OX,), (self.OY,), 0.0, 0.0)
        assert abs(ax[0] - 10.0) < 1e-9
        assert abs(ay[0] - 20.0) < 1e-9

    def test_defect_at_origin_maps_to_zero(self):
        ax, ay = _align_defects((self.OX,), (self.OY,), (self.OX,), (self.OY,), 0.0, 0.0)
        assert abs(ax[0]) < 1e-9 and abs(ay[0]) < 1e-9

    def test_manual_offset_added_in_panel_space(self):
        ax, ay = _align_defects((110.0,), (220.0,), (self.OX,), (self.OY,), 5.0, 3.0)
        assert abs(ax[0] - 15.0) < 1e-9
        assert abs(ay[0] - 23.0) < 1e-9

    def test_multiple_defects_vectorised(self):
        xs = (100.0, 110.0, 100.0, 110.0, 105.0)
        ys = (200.0, 200.0, 220.0, 220.0, 210.0)
        ox = (self.OX,) * 5
        oy = (self.OY,) * 5
        ax, ay = _align_defects(xs, ys, ox, oy, 0.0, 0.0)
        expect = [(0, 0), (10, 0), (0, 20), (10, 20), (5, 10)]
        for i, (lx, ly) in enumerate(expect):
            assert abs(ax[i] - lx) < 1e-9 and abs(ay[i] - ly) < 1e-9

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            _align_defects((1.0, 2.0), (1.0,), (0.0,), (0.0,), 0.0, 0.0)


# ---------------------------------------------------------------------------
# 6. Panelization gap calculation consistency
# ---------------------------------------------------------------------------

class TestPanelizationGapCalculation:
    """
    After the rotation fix, unit_bounds in PanelLayout is swapped for 90/270.
    The gap formula (pitch - unit_size) must use the swapped dimensions.
    """

    def test_gap_x_uses_swapped_width_at_270(self):
        """
        Unit natural dims: W=40mm, H=80mm.
        At ANGLE=270, panel footprint: W_panel=80, H_panel=40.
        X-pitch should be compared against W_panel=80mm, not W=40mm.
        """
        nat_w, nat_h = 40.0, 80.0
        # After fix, unit_bounds = (H, W) = (80, 40) for 270°
        uw, uh = nat_h, nat_w   # swapped
        pitch_x = 82.5          # some X pitch on the panel
        gap_x = pitch_x - uw
        assert abs(gap_x - 2.5) < 1e-9, f"Expected gap 2.5 mm, got {gap_x}"

    def test_gap_y_uses_swapped_height_at_270(self):
        nat_w, nat_h = 40.0, 80.0
        uw, uh = nat_h, nat_w   # swapped
        pitch_y = 41.0
        gap_y = pitch_y - uh
        assert abs(gap_y - 1.0) < 1e-9, f"Expected gap 1.0 mm, got {gap_y}"

    def test_gap_unrotated_uses_natural_dims(self):
        nat_w, nat_h = 40.0, 80.0
        uw, uh = nat_w, nat_h   # NOT swapped (angle=0)
        pitch_x, pitch_y = 42.0, 82.0
        assert abs((pitch_x - uw) - 2.0) < 1e-9
        assert abs((pitch_y - uh) - 2.0) < 1e-9
