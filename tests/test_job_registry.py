"""Tests for job_registry.py — Job persistence and trend analysis."""

import sys
import os
import tempfile
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import job_registry


@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path):
    """Redirect database to a temp directory for test isolation."""
    test_db = tmp_path / 'test_jobs.db'
    with patch.object(job_registry, '_DB_PATH', test_db), \
         patch.object(job_registry, '_DB_DIR', tmp_path):
        yield


class TestRegisterJob:
    def test_basic_registration(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short', 'Nick'],
            'UNIT_INDEX_X': [0, 1],
            'UNIT_INDEX_Y': [0, 1],
            'BUILDUP': [1, 2],
            'SIDE': ['F', 'B'],
        })
        ok = job_registry.register_job('JOB-001', 'Panel-01', '2026-03-27', 'abc123', df)
        assert ok is True

    def test_duplicate_registration(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short'],
            'UNIT_INDEX_X': [0],
            'UNIT_INDEX_Y': [0],
            'BUILDUP': [1],
            'SIDE': ['F'],
        })
        job_registry.register_job('JOB-DUP', 'P1', '2026-03-27', 'hash1', df)
        ok = job_registry.register_job('JOB-DUP', 'P1', '2026-03-27', 'hash1', df)
        assert ok is False


class TestListJobs:
    def test_empty_registry(self):
        jobs = job_registry.list_jobs()
        assert isinstance(jobs, pd.DataFrame)

    def test_lists_registered_jobs(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short'],
            'UNIT_INDEX_X': [0],
            'UNIT_INDEX_Y': [0],
        })
        job_registry.register_job('JOB-LIST', 'P1', '2026-03-27', 'h1', df)
        jobs = job_registry.list_jobs()
        assert len(jobs) >= 1
        assert 'JOB-LIST' in jobs['job_id'].values


class TestQueryTrends:
    def test_empty_trends(self):
        trends = job_registry.query_trends()
        assert isinstance(trends, pd.DataFrame)

    def test_trends_after_registration(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short', 'Nick', 'Open'],
            'UNIT_INDEX_X': [0, 0, 1],
            'UNIT_INDEX_Y': [0, 0, 1],
            'BUILDUP': [1, 1, 2],
            'SIDE': ['F', 'F', 'B'],
        })
        job_registry.register_job('TREND-1', 'P1', '2026-03-27', 't1', df)
        trends = job_registry.query_trends(job_ids=['TREND-1'])
        assert len(trends) > 0
        assert 'defect_type' in trends.columns


class TestGetJobDensitySummary:
    def test_density_summary(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short'] * 5,
            'UNIT_INDEX_X': [0, 0, 1, 1, 1],
            'UNIT_INDEX_Y': [0, 0, 1, 1, 1],
            'BUILDUP': [1] * 5,
            'SIDE': ['F'] * 5,
        })
        job_registry.register_job('DENS-1', 'P1', '2026-03-27', 'd1', df)
        density = job_registry.get_job_density_summary()
        assert not density.empty
        assert 'total_defects' in density.columns
