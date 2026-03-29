"""Tests for export.py — Export pipeline."""

import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from export import export_unit_csv


class TestExportUnitCsv:
    def test_empty_df(self):
        csv = export_unit_csv(pd.DataFrame())
        assert 'unit_x' in csv
        assert 'verified_count' in csv

    def test_basic_export(self):
        df = pd.DataFrame({
            'UNIT_INDEX_X': [0, 0, 1],
            'UNIT_INDEX_Y': [0, 0, 1],
            'DEFECT_TYPE': ['Short', 'Short', 'Nick'],
            'BUILDUP': [1, 1, 2],
            'VERIFICATION': ['CU14', 'F', 'F'],
        })
        csv = export_unit_csv(df)
        lines = csv.strip().split('\n')
        assert len(lines) >= 2  # header + at least 1 data row
        assert 'unit_x' in lines[0]
        assert 'verified_count' in lines[0]

    def test_no_verification_column(self):
        df = pd.DataFrame({
            'UNIT_INDEX_X': [0, 0],
            'UNIT_INDEX_Y': [0, 0],
            'DEFECT_TYPE': ['Short', 'Nick'],
        })
        csv = export_unit_csv(df)
        assert 'unverified_count' in csv
