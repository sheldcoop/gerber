"""Tests for clustering.py — Defect cluster intelligence."""

import sys
import os

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from clustering import compute_clusters, get_cluster_summary, _grid_cluster_fallback


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
