"""Tests for aoi_loader.py — AOI defect data loading and column mapping."""

import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from aoi_loader import (
    _normalize_col_name,
    _auto_map_columns,
    _extract_buildup_side,
    _load_single_aoi,
    COLUMN_ALIASES,
    REQUIRED_COLUMNS,
    FILENAME_PATTERN,
)


# ═══════════════════════════════════════════════════════════════════════════
# Column normalization
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalizeColName:
    def test_lowercase(self):
        assert _normalize_col_name('DEFECT_TYPE') == 'defect_type'

    def test_strip_whitespace(self):
        assert _normalize_col_name('  X Coordinates  ') == 'x_coordinates'

    def test_replace_separators(self):
        assert _normalize_col_name('defect-type') == 'defect_type'
        assert _normalize_col_name('defect type') == 'defect_type'

    def test_multiple_separators(self):
        assert _normalize_col_name('unit__index__x') == 'unit_index_x'


# ═══════════════════════════════════════════════════════════════════════════
# Column auto-mapping
# ═══════════════════════════════════════════════════════════════════════════

class TestAutoMapColumns:
    def test_canonical_names_unchanged(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short'],
            'X_COORDINATES': [1000],
            'Y_COORDINATES': [2000],
        })
        mapped, warnings = _auto_map_columns(df)
        assert 'DEFECT_TYPE' in mapped.columns
        assert len(warnings) == 0

    def test_alias_mapping(self):
        df = pd.DataFrame({
            'defect type': ['Short'],
            'x_coord': [1000],
            'y_coord': [2000],
        })
        mapped, warnings = _auto_map_columns(df)
        assert 'DEFECT_TYPE' in mapped.columns
        assert 'X_COORDINATES' in mapped.columns
        assert 'Y_COORDINATES' in mapped.columns
        assert len(warnings) == 0

    def test_missing_required_warning(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short'],
            # Missing X and Y
            'OTHER_COL': [123],
        })
        mapped, warnings = _auto_map_columns(df)
        assert len(warnings) > 0
        assert 'missing' in warnings[0].lower()

    def test_no_duplicate_mapping(self):
        """If multiple columns match the same canonical, only first is mapped."""
        df = pd.DataFrame({
            'x': [1],
            'x_coord': [2],
            'DEFECT_TYPE': ['Short'],
            'Y_COORDINATES': [3],
        })
        mapped, _ = _auto_map_columns(df)
        # Only one X_COORDINATES column should exist
        assert list(mapped.columns).count('X_COORDINATES') <= 1


# ═══════════════════════════════════════════════════════════════════════════
# Buildup/side extraction
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractBuildupSide:
    def test_standard_format(self):
        bu, side, warnings = _extract_buildup_side('BU-02F.xlsx')
        assert bu == 2
        assert side == 'F'
        assert len(warnings) == 0

    def test_back_side(self):
        bu, side, _ = _extract_buildup_side('BU-01B.xlsx')
        assert bu == 1
        assert side == 'B'

    def test_space_before_side(self):
        bu, side, _ = _extract_buildup_side('BU-02 F.xlsx')
        assert bu == 2
        assert side == 'F'

    def test_underscore_separator(self):
        bu, side, _ = _extract_buildup_side('BU_03F_defects.xlsx')
        assert bu == 3
        assert side == 'F'

    def test_no_separator(self):
        bu, side, _ = _extract_buildup_side('BU02F.xlsx')
        assert bu == 2
        assert side == 'F'

    def test_lowercase(self):
        bu, side, _ = _extract_buildup_side('bu-01f.xlsx')
        assert bu == 1
        assert side == 'F'

    def test_no_match_defaults(self):
        bu, side, warnings = _extract_buildup_side('random_file.xlsx')
        assert bu == 0
        assert side == 'F'
        assert len(warnings) > 0

    def test_two_digit_buildup(self):
        bu, side, _ = _extract_buildup_side('BU-12F.xlsx')
        assert bu == 12


# ═══════════════════════════════════════════════════════════════════════════
# Filename pattern regex
# ═══════════════════════════════════════════════════════════════════════════

class TestFilenamePattern:
    @pytest.mark.parametrize("filename,expected_bu,expected_side", [
        ('BU-02F.xlsx', 2, 'F'),
        ('BU-02B.xlsx', 2, 'B'),
        ('BU-02 F.xlsx', 2, 'F'),
        ('bu-1f.xlsx', 1, 'F'),
        ('BU02F.xlsx', 2, 'F'),
        ('BU_03B.xlsx', 3, 'B'),
    ])
    def test_pattern_matches(self, filename, expected_bu, expected_side):
        match = FILENAME_PATTERN.search(filename)
        assert match is not None
        assert int(match.group(1)) == expected_bu
        assert match.group(2).upper() == expected_side


# ═══════════════════════════════════════════════════════════════════════════
# Single file loader
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadSingleAoi:
    def _make_excel_bytes(self, df: pd.DataFrame) -> bytes:
        """Create Excel file bytes from a DataFrame."""
        import io
        buf = io.BytesIO()
        df.to_excel(buf, index=False, engine='openpyxl')
        return buf.getvalue()

    def test_basic_load(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short', 'Nick'],
            'X_COORDINATES': [10000, 20000],
            'Y_COORDINATES': [15000, 25000],
        })
        result = _load_single_aoi(
            self._make_excel_bytes(df), 'BU-01F.xlsx', 1, 'F'
        )
        assert not result.df.empty
        assert 'X_MM' in result.df.columns
        assert 'Y_MM' in result.df.columns
        # Verify micron → mm conversion
        assert abs(result.df['X_MM'].iloc[0] - 10.0) < 1e-6
        assert abs(result.df['Y_MM'].iloc[0] - 15.0) < 1e-6

    def test_metadata_columns_added(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short'],
            'X_COORDINATES': [10000],
            'Y_COORDINATES': [15000],
        })
        result = _load_single_aoi(
            self._make_excel_bytes(df), 'BU-02B.xlsx', 2, 'B'
        )
        assert result.df['BUILDUP'].iloc[0] == 2
        assert result.df['SIDE'].iloc[0] == 'B'
        assert result.df['SOURCE_FILE'].iloc[0] == 'BU-02B.xlsx'

    def test_invalid_excel_returns_empty(self):
        result = _load_single_aoi(b'not excel data', 'bad.xlsx', 0, 'F')
        assert result.df.empty
        assert len(result.warnings) > 0

    def test_nan_coordinates_dropped(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short', 'Nick', 'Open'],
            'X_COORDINATES': [10000, None, 30000],
            'Y_COORDINATES': [15000, 25000, None],
        })
        result = _load_single_aoi(
            self._make_excel_bytes(df), 'test.xlsx', 1, 'F'
        )
        # Only the first row should survive (others have NaN coords)
        assert len(result.df) == 1
