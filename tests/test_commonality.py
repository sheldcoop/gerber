"""
tests/test_commonality.py — Unit tests for the Unit Commonality data layer.

Covers the pure helpers behind views/unit_commonality.py:
  1. compute_cm_geometry   — origin map + reference cell dimensions
  2. filter_aoi_cm         — buildup / side scope filtering
  3. _align_defects        — rotation-aware mapping + input validation
  4. _compute_pad_fingerprint — fault-site recurrence (incl. the verif/type
                               index-alignment regression)
  5. compute_unit_positions — dominant_angle provenance (cluster vs unit vs none)
"""

import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.data_utils import (
    compute_cm_geometry,
    filter_aoi_cm,
    _align_defects,
    _compute_pad_fingerprint,
)


# ---------------------------------------------------------------------------
# 1. compute_cm_geometry
# ---------------------------------------------------------------------------

class TestComputeCmGeometry:
    def test_origins_map_sorted_indices(self):
        # 2x2 grid of unit positions
        positions = ((10.0, 5.0), (30.0, 5.0), (10.0, 25.0), (30.0, 25.0))
        origins, cw, ch = compute_cm_geometry(
            unit_positions=positions,
            first_layer_bounds=(0.0, 0.0, 15.0, 18.0),
            unit_bounds=(15.0, 18.0),
        )
        # (row, col) -> (x, y) by sorted unique values
        assert origins[(0, 0)] == (10.0, 5.0)
        assert origins[(0, 1)] == (30.0, 5.0)
        assert origins[(1, 0)] == (10.0, 25.0)
        assert origins[(1, 1)] == (30.0, 25.0)

    def test_unit_bounds_preferred_for_cell_dims(self):
        _, cw, ch = compute_cm_geometry(
            unit_positions=((0.0, 0.0),),
            first_layer_bounds=(0.0, 0.0, 99.0, 99.0),  # should be ignored
            unit_bounds=(15.0, 18.0),
        )
        assert (cw, ch) == (15.0, 18.0)

    def test_falls_back_to_layer_bounds_without_unit_bounds(self):
        _, cw, ch = compute_cm_geometry(
            unit_positions=((0.0, 0.0),),
            first_layer_bounds=(2.0, 3.0, 17.0, 21.0),
            unit_bounds=None,
        )
        assert cw == pytest.approx(15.0)
        assert ch == pytest.approx(18.0)

    def test_zero_unit_bounds_falls_back(self):
        _, cw, ch = compute_cm_geometry(
            unit_positions=((0.0, 0.0),),
            first_layer_bounds=(0.0, 0.0, 12.0, 9.0),
            unit_bounds=(0.0, 0.0),
        )
        assert (cw, ch) == (12.0, 9.0)


# ---------------------------------------------------------------------------
# 2. filter_aoi_cm
# ---------------------------------------------------------------------------

class TestFilterAoiCm:
    def _df(self):
        return pd.DataFrame({
            'BUILDUP': [1, 1, 2, 2],
            'SIDE': ['F', 'B', 'F', 'B'],
            'X_MM': [1, 2, 3, 4],
        })

    def test_buildup_filter(self):
        out = filter_aoi_cm(self._df(), (1,), ('Front', 'Back'))
        assert set(out['BUILDUP']) == {1}
        assert len(out) == 2

    def test_front_only(self):
        out = filter_aoi_cm(self._df(), (), ('Front',))
        assert set(out['SIDE']) == {'F'}

    def test_back_only(self):
        out = filter_aoi_cm(self._df(), (), ('Back',))
        assert set(out['SIDE']) == {'B'}

    def test_both_sides_no_side_filter(self):
        out = filter_aoi_cm(self._df(), (), ('Front', 'Back'))
        assert len(out) == 4

    def test_empty_buildup_filter_keeps_all(self):
        out = filter_aoi_cm(self._df(), (), ('Front', 'Back'))
        assert len(out) == 4


# ---------------------------------------------------------------------------
# 3. _align_defects — rotation-aware + validation
# ---------------------------------------------------------------------------

class TestAlignDefects:
    def test_identity_at_zero_angle(self):
        ax, ay = _align_defects((110.0,), (220.0,), (100.0,), (200.0,), 0.0, 0.0)
        assert ax == pytest.approx((10.0,))
        assert ay == pytest.approx((20.0,))

    def test_inverse_rotation_270_roundtrip(self):
        ox, oy, lx, ly = 100.0, 200.0, 10.0, 20.0
        a = math.radians(270)
        px = ox + lx * math.cos(a) - ly * math.sin(a)
        py = oy + lx * math.sin(a) + ly * math.cos(a)
        ax, ay = _align_defects((px,), (py,), (ox,), (oy,), 0.0, 0.0, unit_angle=270)
        assert ax[0] == pytest.approx(lx)
        assert ay[0] == pytest.approx(ly)

    def test_non_orthogonal_angle_is_identity(self):
        # 45° is not a handled orthogonal angle → falls through to identity delta
        ax, ay = _align_defects((5.0,), (7.0,), (0.0,), (0.0,), 0.0, 0.0, unit_angle=45)
        assert ax == pytest.approx((5.0,))
        assert ay == pytest.approx((7.0,))

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            _align_defects((1.0, 2.0), (1.0,), (0.0,), (0.0,), 0.0, 0.0)


# ---------------------------------------------------------------------------
# 4. _compute_pad_fingerprint
# ---------------------------------------------------------------------------

class TestPadFingerprint:
    def test_empty_input_returns_empty(self):
        df = _compute_pad_fingerprint((), (), (), (), ())
        assert df.empty

    def test_unit_count_and_pct(self):
        # Two defects at the same snapped site from two distinct units → 100%
        df = _compute_pad_fingerprint(
            ax_tuple=(1.0, 1.05), ay_tuple=(1.0, 1.0),
            defect_types=('Open', 'Open'),
            unit_keys=((0, 0), (1, 1)),
            buildup_vals=(1, 1),
            verification_vals=('CU22', 'CU22'),
            verif_severity_map=(('CU22', 3),),
        )
        assert len(df) == 1
        assert int(df.iloc[0]['unit_count']) == 2
        assert df.iloc[0]['unit_pct'] == pytest.approx(100.0)
        assert int(df.iloc[0]['defect_count']) == 2

    def test_severity_is_worst_at_site(self):
        # One critical-coded defect + one benign None-coded defect → severity = worst
        df = _compute_pad_fingerprint(
            ax_tuple=(2.0, 2.0), ay_tuple=(2.0, 2.0),
            defect_types=('Open', 'Open'),
            unit_keys=((0, 0), (1, 1)),
            buildup_vals=(1, 1),
            verification_vals=('CU22', None),
            verif_severity_map=(('CU22', 3),),
        )
        assert int(df.iloc[0]['severity']) == 3
        assert df.iloc[0]['top_verif'] == 'CU22'

    def test_verif_type_index_alignment_regression(self):
        """Regression: severity must pair each defect's OWN verification with its
        OWN type. A None code in the middle must not shift later codes onto the
        wrong defect. Here only the 2nd defect carries the critical code."""
        df = _compute_pad_fingerprint(
            ax_tuple=(3.0, 3.0, 3.0), ay_tuple=(3.0, 3.0, 3.0),
            defect_types=('Nick', 'Open', 'Nick'),
            unit_keys=((0, 0), (1, 1), (2, 2)),
            buildup_vals=(1, 1, 1),
            verification_vals=(None, 'CU22', None),
            verif_severity_map=(('CU22', 3),),
        )
        assert len(df) == 1
        assert int(df.iloc[0]['severity']) == 3
        assert df.iloc[0]['top_verif'] == 'CU22'
        assert df.iloc[0]['all_verif'] == 'CU22'

    def test_grid_snapping_groups_nearby(self):
        # Two points within 0.5mm snap to the same site; a far one is separate
        df = _compute_pad_fingerprint(
            ax_tuple=(1.0, 1.2, 9.0), ay_tuple=(1.0, 1.1, 9.0),
            defect_types=('Open', 'Open', 'Open'),
            unit_keys=((0, 0), (1, 1), (2, 2)),
            buildup_vals=(1, 1, 1),
            verification_vals=('A', 'A', 'A'),
        )
        assert len(df) == 2


# ---------------------------------------------------------------------------
# 5. dominant_angle provenance — REAL pipeline (cluster vs unit vs none)
# ---------------------------------------------------------------------------

class TestRotationProvenance:
    """compute_unit_positions must capture rotation declared at ANY hierarchy level,
    and be 0 when there is none. This exercises the real _expand cumulative-angle
    code, not a reimplementation."""

    def _hierarchy(self, panel_to_cluster_angle, cluster_to_unit_angle):
        from odb.models import StepRepeat
        return {
            "panel": [StepRepeat(child_step="cluster", x=0, y=0, dx=0, dy=0,
                                 nx=1, ny=1, angle=panel_to_cluster_angle)],
            "cluster": [StepRepeat(child_step="unit", x=0, y=0, dx=0, dy=0,
                                   nx=1, ny=1, angle=cluster_to_unit_angle)],
        }

    def _layout(self, hier):
        from core.step_layout import compute_unit_positions
        return compute_unit_positions(hier, unit_bounds=(40.0, 80.0),
                                      panel_width=200.0, panel_height=200.0)

    def test_cluster_level_rotation_detected(self):
        pl = self._layout(self._hierarchy(270.0, 0.0))
        assert pl.dominant_angle == 270.0

    def test_unit_level_rotation_detected(self):
        pl = self._layout(self._hierarchy(0.0, 270.0))
        assert pl.dominant_angle == 270.0

    def test_no_rotation_is_zero(self):
        pl = self._layout(self._hierarchy(0.0, 0.0))
        assert pl.dominant_angle == 0.0
        # unit_bounds is always stored as the natural (unswapped) footprint
        assert pl.unit_bounds == (40.0, 80.0)

    def test_combined_rotation_is_cumulative(self):
        # 90 at cluster + 180 at unit → 270 cumulative
        pl = self._layout(self._hierarchy(90.0, 180.0))
        assert pl.dominant_angle == 270.0
