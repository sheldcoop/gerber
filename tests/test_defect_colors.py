"""Tests for constant, distinct verification-code colours (viz/defects.py)."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.constants import VERIFICATION_CODE_COLORS, DEFECT_TYPE_COLORS
from viz.defects import _stable_group_color


class TestVerificationCodeColors:
    def test_cu22_and_cu18_are_distinct(self):
        assert VERIFICATION_CODE_COLORS['CU22'] != VERIFICATION_CODE_COLORS['CU18']

    def test_all_predefined_colors_unique(self):
        colors = list(VERIFICATION_CODE_COLORS.values())
        assert len(colors) == len(set(colors)), "verification colours must be distinct"

    def test_lookup_returns_predefined(self):
        assert _stable_group_color('CU22') == VERIFICATION_CODE_COLORS['CU22']
        assert _stable_group_color('CU18') == VERIFICATION_CODE_COLORS['CU18']

    def test_unknown_code_is_deterministic(self):
        # Same input → same colour every call (and across processes, since it
        # uses md5 rather than the randomised built-in hash()).
        first = _stable_group_color('SOME_NEW_CODE')
        second = _stable_group_color('SOME_NEW_CODE')
        assert first == second
        assert first in DEFECT_TYPE_COLORS
