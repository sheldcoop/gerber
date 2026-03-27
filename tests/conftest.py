"""Shared fixtures for ODB++ VRS inspection tool tests."""

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box as shapely_box, Point

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from alignment import AlignmentResult, GeometryContext, calculate_geometry
from odb_parser import ODBLayer, ParsedODB


# ---------------------------------------------------------------------------
# Geometry fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_geometry() -> GeometryContext:
    """Standard panel geometry with default params: 6x6 units, dyn_gap 5.0/3.5."""
    return calculate_geometry(panel_rows=6, panel_cols=6, dyn_gap_x=5.0, dyn_gap_y=3.5)


@pytest.fixture
def simple_geometry() -> GeometryContext:
    """Minimal 2x2 panel geometry with zero dynamic gap."""
    return calculate_geometry(panel_rows=2, panel_cols=2, dyn_gap_x=0.0, dyn_gap_y=0.0)


# ---------------------------------------------------------------------------
# ODB++ fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_odb_layer() -> ODBLayer:
    """Simple ODB layer with a few rectangular polygons."""
    polys = [
        shapely_box(0, 0, 10, 10),
        shapely_box(15, 15, 20, 20),
        shapely_box(25, 0, 30, 5),
    ]
    return ODBLayer(
        name='test_copper',
        layer_type='copper',
        polygons=polys,
        bounds=(0, 0, 30, 20),
        polygon_count=3,
    )


@pytest.fixture
def sample_parsed_odb(sample_odb_layer) -> ParsedODB:
    """ParsedODB with one copper layer and known fiducials."""
    outline = ODBLayer(
        name='profile',
        layer_type='outline',
        polygons=[shapely_box(0, 0, 50, 50)],
        bounds=(0, 0, 50, 50),
        polygon_count=1,
    )
    return ParsedODB(
        layers={'test_copper': sample_odb_layer, 'profile': outline},
        board_bounds=(0, 0, 50, 50),
        step_name='pcb',
        units='mm',
        origin_x=0.0,
        origin_y=0.0,
        fiducials=[(5.0, 5.0), (45.0, 5.0), (45.0, 45.0), (5.0, 45.0)],
    )


# ---------------------------------------------------------------------------
# AOI DataFrame fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_aoi_df() -> pd.DataFrame:
    """AOI defect DataFrame with known coordinates (in mm) and metadata."""
    np.random.seed(42)
    n = 100
    return pd.DataFrame({
        'DEFECT_ID': range(1, n + 1),
        'DEFECT_TYPE': np.random.choice(
            ['Short', 'Nick', 'Open', 'Protrusion', 'Space'], size=n
        ),
        'X_COORDINATES': np.random.uniform(5000, 45000, size=n),  # microns
        'Y_COORDINATES': np.random.uniform(5000, 45000, size=n),
        'X_MM': None,  # will be filled
        'Y_MM': None,
        'BUILDUP': np.random.choice([1, 2], size=n),
        'SIDE': np.random.choice(['F', 'B'], size=n),
        'UNIT_INDEX_X': np.random.choice([0, 1, 2, 3], size=n),
        'UNIT_INDEX_Y': np.random.choice([0, 1, 2, 3], size=n),
        'VERIFICATION': np.random.choice(['Y', 'N'], size=n),
        'SOURCE_FILE': 'test_BU-01F.xlsx',
    })


@pytest.fixture
def sample_aoi_df_mm(sample_aoi_df) -> pd.DataFrame:
    """AOI DataFrame with X_MM/Y_MM already populated."""
    df = sample_aoi_df.copy()
    df['X_MM'] = df['X_COORDINATES'] / 1000.0
    df['Y_MM'] = df['Y_COORDINATES'] / 1000.0
    return df


@pytest.fixture
def aoi_df_with_fiducials() -> pd.DataFrame:
    """AOI DataFrame that includes fiducial reference columns."""
    return pd.DataFrame({
        'DEFECT_TYPE': ['Short', 'Nick', 'Open'],
        'X_COORDINATES': [10000, 20000, 30000],
        'Y_COORDINATES': [10000, 20000, 30000],
        'X_MM': [10.0, 20.0, 30.0],
        'Y_MM': [10.0, 20.0, 30.0],
        'FIDUCIAL_X': [5000, 45000, 45000],
        'FIDUCIAL_Y': [5000, 5000, 45000],
    })


# ---------------------------------------------------------------------------
# Alignment fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def identity_alignment() -> AlignmentResult:
    """An identity alignment (no transform)."""
    return AlignmentResult(
        method='offset',
        offset_x=0.0,
        offset_y=0.0,
        overlap_pct=100.0,
        transform_matrix=np.eye(3),
        gerber_bounds=(0, 0, 50, 50),
        aoi_bounds=(0, 0, 50, 50),
    )


@pytest.fixture
def translated_alignment() -> AlignmentResult:
    """Alignment with a known translation offset."""
    m = np.eye(3)
    m[0, 2] = 10.0
    m[1, 2] = -5.0
    return AlignmentResult(
        method='offset',
        offset_x=10.0,
        offset_y=-5.0,
        overlap_pct=80.0,
        transform_matrix=m,
        gerber_bounds=(0, 0, 50, 50),
        aoi_bounds=(0, 0, 50, 50),
    )
