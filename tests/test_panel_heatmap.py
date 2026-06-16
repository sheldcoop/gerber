"""Tests for the panel-heatmap unit-grid helpers.

The key guarantee here is an *output-equality* check: the vectorized
``panels_per_cell_grid`` must produce exactly the same grid as the original
per-cell boolean-mask loop it replaced in ``views/panel_heatmap.py``.
"""
import numpy as np
import pandas as pd
import pytest

from core.data_utils import panels_per_cell_grid


def _naive_panels_per_cell(df, panel_col, n_rows, n_cols):
    """Reference: the exact per-cell mask loop that lived in the heatmap view.

    grid[ri, ci] = unique panels among defects at (UNIT_INDEX_Y==ri, UNIT_INDEX_X==ci).
    """
    grid = np.zeros((n_rows, n_cols), dtype=float)
    has_idx = 'UNIT_INDEX_X' in df.columns and 'UNIT_INDEX_Y' in df.columns
    for ri in range(n_rows):
        for ci in range(n_cols):
            mask = ((df['UNIT_INDEX_X'] == ci) &
                    (df['UNIT_INDEX_Y'] == ri)) if has_idx else None
            n_p = (df.loc[mask, panel_col].nunique()
                   if (mask is not None and panel_col in df.columns) else 0)
            grid[ri, ci] = n_p
    return grid


@pytest.fixture
def sample_df():
    rng = np.random.default_rng(42)
    n = 800
    return pd.DataFrame({
        'UNIT_INDEX_X': rng.integers(0, 6, n),
        'UNIT_INDEX_Y': rng.integers(0, 4, n),
        'PANEL_ID': rng.integers(0, 5, n).astype(str),
        'X_MM': rng.random(n) * 100.0,
        'Y_MM': rng.random(n) * 100.0,
    })


def test_matches_naive_mask_loop(sample_df):
    """Vectorized grid is byte-for-byte identical to the old mask loop."""
    n_cols = int(sample_df['UNIT_INDEX_X'].max()) + 1
    n_rows = int(sample_df['UNIT_INDEX_Y'].max()) + 1
    fast = panels_per_cell_grid(sample_df, 'PANEL_ID', n_rows, n_cols)
    slow = _naive_panels_per_cell(sample_df, 'PANEL_ID', n_rows, n_cols)
    np.testing.assert_array_equal(fast, slow)


@pytest.mark.parametrize("panel_col", ['PANEL_ID', 'SOURCE_FILE'])
def test_matches_naive_with_source_file(panel_col):
    rng = np.random.default_rng(7)
    n = 300
    df = pd.DataFrame({
        'UNIT_INDEX_X': rng.integers(0, 3, n),
        'UNIT_INDEX_Y': rng.integers(0, 3, n),
        'SOURCE_FILE': rng.choice(['a.tgz', 'b.tgz', 'c.tgz'], n),
        'PANEL_ID': rng.choice(['P1', 'P2'], n),
    })
    grid = panels_per_cell_grid(df, panel_col, 3, 3)
    ref = _naive_panels_per_cell(df, panel_col, 3, 3)
    np.testing.assert_array_equal(grid, ref)


def test_known_counts_and_empty_cells():
    df = pd.DataFrame({
        'UNIT_INDEX_X': [0, 0, 0, 2],
        'UNIT_INDEX_Y': [0, 0, 0, 1],
        'PANEL_ID': ['A', 'B', 'A', 'A'],   # cell (0,0): panels {A,B}=2 (3 defects)
    })
    grid = panels_per_cell_grid(df, 'PANEL_ID', 2, 3)
    assert grid[0, 0] == 2        # distinct panels, not defect count
    assert grid[1, 2] == 1
    assert grid[1, 0] == 0        # empty cell
    assert grid[0, 1] == 0        # empty cell


def test_missing_panel_col_returns_zeros():
    df = pd.DataFrame({'UNIT_INDEX_X': [0, 1], 'UNIT_INDEX_Y': [0, 1]})
    grid = panels_per_cell_grid(df, 'PANEL_ID', 2, 2)
    np.testing.assert_array_equal(grid, np.zeros((2, 2)))


def test_missing_index_cols_returns_zeros():
    df = pd.DataFrame({'PANEL_ID': ['A', 'B']})
    grid = panels_per_cell_grid(df, 'PANEL_ID', 2, 2)
    np.testing.assert_array_equal(grid, np.zeros((2, 2)))
