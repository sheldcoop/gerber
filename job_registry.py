"""
job_registry.py — Lightweight job persistence for multi-session trend analysis.

Stores parsed AOI inspection results in SQLite, indexed by [job_id, panel_id, date].
Enables cross-job defect density trending without re-uploading data.

Database location: ~/.cache/gerber-vrs/jobs.db
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_DB_DIR = Path.home() / '.cache' / 'gerber-vrs'
_DB_PATH = _DB_DIR / 'jobs.db'


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    panel_id TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, panel_id, file_hash)
);

CREATE TABLE IF NOT EXISTS defect_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    panel_id TEXT NOT NULL DEFAULT '',
    unit_row INTEGER,
    unit_col INTEGER,
    defect_type TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    buildup INTEGER DEFAULT 0,
    side TEXT DEFAULT 'F',
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE INDEX IF NOT EXISTS idx_defect_job ON defect_summary(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_date ON jobs(date);
"""


def _get_connection() -> sqlite3.Connection:
    """Get (or create) the SQLite database connection."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_CREATE_SQL)
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_job(
    job_id: str,
    panel_id: str,
    date: str,
    file_hash: str,
    defect_df: pd.DataFrame,
) -> bool:
    """Register an inspection job and its defect summary.

    Args:
        job_id: Unique job identifier (e.g. lot number).
        panel_id: Panel identifier within the job.
        date: Inspection date (ISO format, e.g. '2026-03-27').
        file_hash: MD5 hash of the source AOI files.
        defect_df: Full defect DataFrame with DEFECT_TYPE, UNIT_INDEX_X/Y,
                   BUILDUP, SIDE columns.

    Returns:
        True if registered successfully, False if duplicate.
    """
    conn = _get_connection()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO jobs (job_id, panel_id, date, file_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, panel_id, date, file_hash, datetime.now().isoformat()),
        )
        if cursor.rowcount == 0:
            return False

        # Build summary aggregation
        group_cols = ['DEFECT_TYPE']
        if 'UNIT_INDEX_Y' in defect_df.columns:
            group_cols.append('UNIT_INDEX_Y')
        if 'UNIT_INDEX_X' in defect_df.columns:
            group_cols.append('UNIT_INDEX_X')
        if 'BUILDUP' in defect_df.columns:
            group_cols.append('BUILDUP')
        if 'SIDE' in defect_df.columns:
            group_cols.append('SIDE')

        for keys, group in defect_df.groupby(group_cols, observed=True):
            if not isinstance(keys, tuple):
                keys = (keys,)
            key_dict = dict(zip(group_cols, keys))
            conn.execute(
                "INSERT INTO defect_summary "
                "(job_id, panel_id, unit_row, unit_col, defect_type, count, buildup, side) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id, panel_id,
                    key_dict.get('UNIT_INDEX_Y', 0),
                    key_dict.get('UNIT_INDEX_X', 0),
                    key_dict.get('DEFECT_TYPE', ''),
                    len(group),
                    key_dict.get('BUILDUP', 0),
                    key_dict.get('SIDE', 'F'),
                ),
            )

        conn.commit()
        logger.info(f"Registered job {job_id}/{panel_id} with {len(defect_df)} defects")
        return True
    except sqlite3.IntegrityError:
        logger.info(f"Job {job_id}/{panel_id} already registered")
        return False
    finally:
        conn.close()


def list_jobs() -> pd.DataFrame:
    """List all registered jobs.

    Returns:
        DataFrame with columns: job_id, panel_id, date, file_hash, created_at
    """
    conn = _get_connection()
    try:
        df = pd.read_sql_query("SELECT * FROM jobs ORDER BY date DESC, created_at DESC", conn)
        return df
    finally:
        conn.close()


def query_trends(
    job_ids: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Query defect density trends across jobs.

    Args:
        job_ids: Optional filter — only include these jobs.

    Returns:
        DataFrame with columns: job_id, panel_id, unit_row, unit_col,
        defect_type, count, buildup, side. Joined with jobs.date for trending.
    """
    conn = _get_connection()
    try:
        query = """
            SELECT d.job_id, d.panel_id, j.date, d.unit_row, d.unit_col,
                   d.defect_type, d.count, d.buildup, d.side
            FROM defect_summary d
            JOIN jobs j ON d.job_id = j.job_id AND d.panel_id = j.panel_id
        """
        params: list = []
        if job_ids:
            placeholders = ','.join('?' * len(job_ids))
            query += f" WHERE d.job_id IN ({placeholders})"
            params = job_ids

        query += " ORDER BY j.date, d.job_id, d.unit_row, d.unit_col"
        df = pd.read_sql_query(query, conn, params=params)
        return df
    finally:
        conn.close()


def get_job_density_summary(job_ids: Optional[list[str]] = None) -> pd.DataFrame:
    """Get defect density per unit per job for trend charts.

    Returns:
        DataFrame with: job_id, date, unit_row, unit_col, total_defects
    """
    trends = query_trends(job_ids)
    if trends.empty:
        return trends

    summary = trends.groupby(
        ['job_id', 'date', 'unit_row', 'unit_col'], observed=True
    )['count'].sum().reset_index()
    summary.rename(columns={'count': 'total_defects'}, inplace=True)
    return summary
