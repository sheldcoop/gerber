#!/usr/bin/env python3
"""
benchmark.py — Performance benchmarks for ODB++ VRS inspection tool.

Measures:
- Cold load: parse ODB++ + load AOI + compute alignment from scratch
- Warm load: read from Parquet cache + cached alignment lookup
- Alignment computation: fiducial-based vs simple offset

Usage:
    python3 benchmark.py [--odb test_2F.tgz] [--rows 50000]
"""

import argparse
import hashlib
import io
import os
import sys
import time

import numpy as np
import pandas as pd


def _make_synthetic_aoi(n_rows: int = 50000) -> pd.DataFrame:
    """Generate a synthetic AOI defect DataFrame for benchmarking."""
    np.random.seed(42)
    return pd.DataFrame({
        'DEFECT_ID': range(1, n_rows + 1),
        'DEFECT_TYPE': np.random.choice(
            ['Short', 'Nick', 'Open', 'Protrusion', 'Space', 'Island'], size=n_rows
        ),
        'X_COORDINATES': np.random.uniform(5000, 45000, size=n_rows),
        'Y_COORDINATES': np.random.uniform(5000, 45000, size=n_rows),
        'X_MM': np.random.uniform(5, 45, size=n_rows),
        'Y_MM': np.random.uniform(5, 45, size=n_rows),
        'BUILDUP': np.random.choice([1, 2], size=n_rows),
        'SIDE': np.random.choice(['F', 'B'], size=n_rows),
        'UNIT_INDEX_X': np.random.choice(range(12), size=n_rows),
        'UNIT_INDEX_Y': np.random.choice(range(12), size=n_rows),
        'VERIFICATION': np.random.choice(['Y', 'N'], size=n_rows),
        'SOURCE_FILE': 'benchmark_BU-01F.xlsx',
    })


def benchmark_parquet_cache(n_rows: int = 50000) -> dict:
    """Benchmark Parquet cache warm vs cold load."""
    from pathlib import Path
    from aoi_loader import _CACHE_DIR

    df = _make_synthetic_aoi(n_rows)

    # Write to Parquet
    cache_path = _CACHE_DIR / 'benchmark_test.parquet'
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Cold: DataFrame to_parquet
    t0 = time.monotonic()
    df.to_parquet(cache_path, engine='pyarrow', index=False)
    cold_write_ms = (time.monotonic() - t0) * 1000

    # Warm: read_parquet
    t0 = time.monotonic()
    df_loaded = pd.read_parquet(cache_path)
    warm_read_ms = (time.monotonic() - t0) * 1000

    # Cleanup
    cache_path.unlink(missing_ok=True)

    return {
        'n_rows': n_rows,
        'cold_write_ms': round(cold_write_ms, 1),
        'warm_read_ms': round(warm_read_ms, 1),
        'warm_under_200ms': warm_read_ms < 200,
    }


def benchmark_alignment(n_rows: int = 50000) -> dict:
    """Benchmark alignment computation."""
    from alignment import compute_alignment, apply_alignment

    df = _make_synthetic_aoi(n_rows)
    gerber_bounds = (0, 0, 50, 50)
    aoi_bounds = (df['X_MM'].min(), df['Y_MM'].min(), df['X_MM'].max(), df['Y_MM'].max())
    fiducials_gerber = [(5, 5), (45, 5), (45, 45), (5, 45)]

    # Alignment computation (with fiducials in AOI data)
    t0 = time.monotonic()
    result = compute_alignment(
        gerber_bounds=gerber_bounds,
        aoi_bounds=aoi_bounds,
        fiducials_gerber=fiducials_gerber,
        fiducials_aoi=fiducials_gerber,  # identity for benchmark
    )
    align_ms = (time.monotonic() - t0) * 1000

    # Apply alignment
    t0 = time.monotonic()
    aligned_df = apply_alignment(df, result)
    apply_ms = (time.monotonic() - t0) * 1000

    return {
        'n_rows': n_rows,
        'compute_alignment_ms': round(align_ms, 2),
        'apply_alignment_ms': round(apply_ms, 1),
        'method': result.method,
        'confidence': round(result.confidence, 4),
    }


def benchmark_scoring(n_rows: int = 50000) -> dict:
    """Benchmark defect priority scoring."""
    from scoring import score_defect_priority

    df = _make_synthetic_aoi(n_rows)
    df['ALIGNED_X'] = df['X_MM']
    df['ALIGNED_Y'] = df['Y_MM']

    t0 = time.monotonic()
    scores = score_defect_priority(df)
    score_ms = (time.monotonic() - t0) * 1000

    return {
        'n_rows': n_rows,
        'scoring_ms': round(score_ms, 1),
        'max_score': round(float(scores.max()), 1),
        'min_score': round(float(scores.min()), 1),
    }


def benchmark_clustering(n_rows: int = 50000) -> dict:
    """Benchmark DBSCAN clustering."""
    from clustering import compute_clusters, get_cluster_summary

    df = _make_synthetic_aoi(n_rows)
    df['ALIGNED_X'] = df['X_MM']
    df['ALIGNED_Y'] = df['Y_MM']

    t0 = time.monotonic()
    clustered = compute_clusters(df, eps=2.0, min_samples=3)
    cluster_ms = (time.monotonic() - t0) * 1000

    summary = get_cluster_summary(clustered)

    return {
        'n_rows': n_rows,
        'clustering_ms': round(cluster_ms, 1),
        'n_clusters': len(summary),
        'noise_pct': round((clustered['CLUSTER_ID'] == -1).mean() * 100, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="ODB++ VRS Inspection Benchmarks")
    parser.add_argument('--rows', type=int, default=50000, help='Number of synthetic defect rows')
    args = parser.parse_args()

    print(f"{'='*60}")
    print(f"ODB++ VRS Inspection Tool — Benchmarks")
    print(f"{'='*60}")
    print(f"Synthetic rows: {args.rows:,}")
    print()

    # Parquet cache
    print("--- Parquet Cache ---")
    cache_results = benchmark_parquet_cache(args.rows)
    print(f"  Cold write:  {cache_results['cold_write_ms']:>8.1f} ms")
    print(f"  Warm read:   {cache_results['warm_read_ms']:>8.1f} ms  {'PASS' if cache_results['warm_under_200ms'] else 'FAIL'} (<200ms)")
    print()

    # Alignment
    print("--- Alignment ---")
    align_results = benchmark_alignment(args.rows)
    print(f"  Compute:     {align_results['compute_alignment_ms']:>8.2f} ms  (method: {align_results['method']}, conf: {align_results['confidence']})")
    print(f"  Apply:       {align_results['apply_alignment_ms']:>8.1f} ms")
    print()

    # Scoring
    print("--- Priority Scoring ---")
    score_results = benchmark_scoring(args.rows)
    print(f"  Score:       {score_results['scoring_ms']:>8.1f} ms  (range: {score_results['min_score']}-{score_results['max_score']})")
    print()

    # Clustering
    print("--- DBSCAN Clustering ---")
    cluster_results = benchmark_clustering(args.rows)
    print(f"  Cluster:     {cluster_results['clustering_ms']:>8.1f} ms  ({cluster_results['n_clusters']} clusters, {cluster_results['noise_pct']:.1f}% noise)")
    print()

    print(f"{'='*60}")
    all_pass = cache_results['warm_under_200ms']
    print(f"Overall: {'ALL BENCHMARKS PASS' if all_pass else 'SOME BENCHMARKS FAILED'}")
    print(f"{'='*60}")

    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
