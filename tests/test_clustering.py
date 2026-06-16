"""Tests for clustering.py — Defect cluster intelligence."""

import sys
import os

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from clustering import (
    compute_clusters, get_cluster_summary, _grid_cluster_fallback,
    compute_clusters_by_verification, normalize_verification, VERIF_UNVERIFIED,
)


class TestComputeClustersByVerification:
    """Spatial clustering runs WITHIN each verification code: clusters never
    mix codes, IDs are globally unique, blanks form an 'Unverified' group."""

    @staticmethod
    def _spot(n, x, y, verif, seed):
        rng = np.random.default_rng(seed)
        return pd.DataFrame({
            'ALIGNED_X': rng.normal(x, 0.2, n),
            'ALIGNED_Y': rng.normal(y, 0.2, n),
            'VERIFICATION': [verif] * n,
        })

    def test_same_location_different_codes_split(self):
        # Two codes at the SAME spot: plain spatial DBSCAN would merge them;
        # per-code clustering must keep them apart.
        df = pd.concat([self._spot(10, 5, 5, 'CU22', 1),
                        self._spot(10, 5, 5, 'SH', 2)], ignore_index=True)
        out = compute_clusters_by_verification(df, eps=2.0, min_samples=3)
        clustered = out[out['CLUSTER_ID'] >= 0]
        per_cluster_codes = clustered.groupby('CLUSTER_ID')['VERIF_GROUP'].nunique()
        assert (per_cluster_codes == 1).all()          # every cluster one code
        assert clustered.groupby('VERIF_GROUP')['CLUSTER_ID'].first().nunique() == 2

    def test_ids_globally_unique_across_codes(self):
        df = pd.concat([self._spot(10, 5, 5, 'CU22', 1),
                        self._spot(10, 50, 50, 'SH', 2)], ignore_index=True)
        out = compute_clusters_by_verification(df, eps=2.0, min_samples=3)
        ids_a = set(out.loc[out['VERIF_GROUP'] == 'CU22', 'CLUSTER_ID']) - {-1}
        ids_b = set(out.loc[out['VERIF_GROUP'] == 'SH', 'CLUSTER_ID']) - {-1}
        assert ids_a and ids_b and not (ids_a & ids_b)

    def test_blanks_form_unverified_group(self):
        df = self._spot(10, 5, 5, 'CU22', 1)
        df.loc[:4, 'VERIFICATION'] = [None, '', '—', 'nan', 'CU22'][:5]
        out = compute_clusters_by_verification(df, eps=2.0, min_samples=2)
        assert set(out['VERIF_GROUP']) == {'CU22', VERIF_UNVERIFIED}

    def test_missing_verification_column(self):
        df = self._spot(10, 5, 5, 'x', 1).drop(columns=['VERIFICATION'])
        out = compute_clusters_by_verification(df, eps=2.0, min_samples=3)
        assert (out['VERIF_GROUP'] == VERIF_UNVERIFIED).all()
        assert (out['CLUSTER_ID'] >= 0).any()

    def test_empty_df(self):
        df = pd.DataFrame(columns=['ALIGNED_X', 'ALIGNED_Y', 'VERIFICATION'])
        out = compute_clusters_by_verification(df)
        assert 'CLUSTER_ID' in out.columns and out.empty


def test_normalize_verification_placeholders():
    s = pd.Series(['CU22', ' SH ', '', '—', None, float('nan')])
    out = normalize_verification(s)
    assert out.tolist() == ['CU22', 'SH', VERIF_UNVERIFIED, VERIF_UNVERIFIED,
                            VERIF_UNVERIFIED, VERIF_UNVERIFIED]


class TestComputeClusters:
    def test_empty_df(self):
        df = pd.DataFrame(columns=['ALIGNED_X', 'ALIGNED_Y'])
        result = compute_clusters(df)
        assert 'CLUSTER_ID' in result.columns

    def test_too_few_points(self):
        df = pd.DataFrame({'ALIGNED_X': [1.0], 'ALIGNED_Y': [1.0]})
        result = compute_clusters(df, min_samples=3)
        assert all(result['CLUSTER_ID'] == -1)

    def test_clear_clusters(self):
        """Two tight clusters should be identified."""
        np.random.seed(42)
        n = 20
        # Cluster 1: around (10, 10)
        c1_x = np.random.normal(10, 0.3, n)
        c1_y = np.random.normal(10, 0.3, n)
        # Cluster 2: around (50, 50) — well separated
        c2_x = np.random.normal(50, 0.3, n)
        c2_y = np.random.normal(50, 0.3, n)

        df = pd.DataFrame({
            'ALIGNED_X': np.concatenate([c1_x, c2_x]),
            'ALIGNED_Y': np.concatenate([c1_y, c2_y]),
            'DEFECT_TYPE': ['Short'] * 2 * n,
        })
        result = compute_clusters(df, eps=2.0, min_samples=3)
        unique_clusters = result['CLUSTER_ID'].unique()
        # Should find at least 2 clusters (excluding noise -1)
        real_clusters = [c for c in unique_clusters if c >= 0]
        assert len(real_clusters) >= 2

    def test_all_noise(self):
        """Widely spaced points should all be noise."""
        df = pd.DataFrame({
            'ALIGNED_X': [0, 100, 200, 300],
            'ALIGNED_Y': [0, 100, 200, 300],
        })
        result = compute_clusters(df, eps=2.0, min_samples=3)
        assert all(result['CLUSTER_ID'] == -1)


class TestGridClusterFallback:
    def test_basic_clustering(self):
        df = pd.DataFrame({
            'ALIGNED_X': [1.0, 1.1, 1.2, 50.0],
            'ALIGNED_Y': [1.0, 1.1, 1.2, 50.0],
        })
        result = _grid_cluster_fallback(df, cell_size=2.0, min_count=3)
        assert 'CLUSTER_ID' in result.columns
        # First 3 should be in a cluster, last should be noise
        assert result.iloc[3]['CLUSTER_ID'] == -1


class TestGetClusterSummary:
    def test_no_clusters(self):
        df = pd.DataFrame({
            'ALIGNED_X': [1, 2],
            'ALIGNED_Y': [1, 2],
            'CLUSTER_ID': [-1, -1],
            'DEFECT_TYPE': ['Short', 'Nick'],
        })
        summary = get_cluster_summary(df)
        assert summary.empty

    def test_with_clusters(self):
        df = pd.DataFrame({
            'ALIGNED_X': [10, 10.1, 10.2, 50, 50.1, 50.2],
            'ALIGNED_Y': [10, 10.1, 10.2, 50, 50.1, 50.2],
            'CLUSTER_ID': [0, 0, 0, 1, 1, 1],
            'DEFECT_TYPE': ['Short', 'Short', 'Nick', 'Open', 'Open', 'Open'],
            'BUILDUP': [1, 1, 1, 2, 2, 2],
            'SIDE': ['F', 'F', 'F', 'B', 'B', 'B'],
        })
        summary = get_cluster_summary(df)
        assert len(summary) == 2
        assert 'cluster_id' in summary.columns
        assert 'defect_count' in summary.columns
        assert 'dominant_type' in summary.columns
        # Sorted by defect_count desc
        assert summary.iloc[0]['defect_count'] >= summary.iloc[1]['defect_count']

    def test_all_nan_defect_type_no_crash(self):
        """Regression: a cluster whose DEFECT_TYPE is entirely NaN must not raise
        IndexError on value_counts().index[0] — it falls back to 'Unknown'."""
        df = pd.DataFrame({
            'ALIGNED_X': [10, 10.1, 10.2],
            'ALIGNED_Y': [10, 10.1, 10.2],
            'CLUSTER_ID': [0, 0, 0],
            'DEFECT_TYPE': [np.nan, np.nan, np.nan],
        })
        summary = get_cluster_summary(df)
        assert len(summary) == 1
        assert summary.iloc[0]['dominant_type'] == 'Unknown'
