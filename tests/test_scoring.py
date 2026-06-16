"""Tests for scoring.py — Defect priority scoring."""

import sys
import os

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scoring import classify_severity, score_defect_priority, build_severity_map


class TestClassifySeverity:
    def test_critical(self):
        assert classify_severity('Short') == 3
        assert classify_severity('Open Circuit') == 3
        assert classify_severity('Missing Component') == 3

    def test_high(self):
        assert classify_severity('Space Violation') == 2
        assert classify_severity('Pinhole') == 2

    def test_medium(self):
        assert classify_severity('Nick') == 1
        assert classify_severity('Scratch Mark') == 1

    def test_low(self):
        assert classify_severity('Protrusion') == 0
        assert classify_severity('Residue') == 0

    def test_unknown_defaults_medium(self):
        assert classify_severity('UnknownType123') == 1

    def test_case_insensitive(self):
        assert classify_severity('SHORT') == 3
        assert classify_severity('short') == 3


class TestScoreDefectPriority:
    def test_empty_df(self):
        result = score_defect_priority(pd.DataFrame())
        assert len(result) == 0

    def test_basic_scoring(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short', 'Protrusion'],
            'ALIGNED_X': [10, 20],
            'ALIGNED_Y': [10, 20],
            'BUILDUP': [1, 1],
        })
        scores = score_defect_priority(df)
        assert len(scores) == 2
        assert scores.iloc[0] > scores.iloc[1]  # Short > Protrusion

    def test_buildup_weight(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Nick', 'Nick'],
            'ALIGNED_X': [10, 20],
            'ALIGNED_Y': [10, 20],
            'BUILDUP': [1, 5],
        })
        scores = score_defect_priority(df)
        assert scores.iloc[1] > scores.iloc[0]  # Higher buildup = higher score

    def test_density_bonus(self):
        # Two clusters: 5 defects close together, 1 isolated
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Nick'] * 6,
            'ALIGNED_X': [10.0, 10.1, 10.2, 10.3, 10.4, 100.0],
            'ALIGNED_Y': [10.0, 10.1, 10.2, 10.3, 10.4, 100.0],
            'BUILDUP': [1] * 6,
        })
        scores = score_defect_priority(df)
        # Clustered defects should have higher scores than isolated
        assert scores.iloc[0] > scores.iloc[5]


class TestBuildSeverityMap:
    def test_returns_labels(self):
        smap = build_severity_map(['Short', 'Nick', 'Protrusion'])
        assert smap['Short'] == 'Critical'
        assert smap['Nick'] == 'Medium'
        assert smap['Protrusion'] == 'Low'
