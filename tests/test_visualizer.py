"""Tests for visualizer.py — Plotly figure builder for PCB overlay."""

import sys
import os

import numpy as np
import pandas as pd
import pytest
import plotly.graph_objects as go
from shapely.geometry import box as shapely_box

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from visualizer import (
    _polygon_to_coords,
    _geometry_to_coords,
    _build_severity_map,
    _build_hover_template,
    _build_customdata,
    build_defect_only_figure,
    OverlayConfig,
    LAYER_COLORS,
    DEFECT_TYPE_COLORS,
    _smart_tick,
)
from odb_parser import ODBLayer


# ═══════════════════════════════════════════════════════════════════════════
# Shapely → Plotly coordinate conversion
# ═══════════════════════════════════════════════════════════════════════════

class TestPolygonToCoords:
    def test_simple_rectangle(self):
        poly = shapely_box(0, 0, 10, 10)
        xs, ys = _polygon_to_coords(poly)
        # Should have 5 points (closed ring) + None separator
        assert None in xs
        assert None in ys
        # Non-None values should cover the rectangle
        x_vals = [x for x in xs if x is not None]
        y_vals = [y for y in ys if y is not None]
        assert min(x_vals) == 0.0
        assert max(x_vals) == 10.0

    def test_polygon_with_hole(self):
        from shapely.geometry import Polygon
        exterior = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
        hole = [(5, 5), (15, 5), (15, 15), (5, 15), (5, 5)]
        poly = Polygon(exterior, [hole])
        xs, ys = _polygon_to_coords(poly)
        # Should have at least 2 None separators (exterior + hole)
        none_count = xs.count(None)
        assert none_count >= 2


class TestGeometryToCoords:
    def test_single_polygon(self):
        poly = shapely_box(0, 0, 5, 5)
        xs, ys = _geometry_to_coords(poly)
        assert len(xs) > 0
        assert len(ys) > 0

    def test_multipolygon(self):
        from shapely.geometry import MultiPolygon
        mp = MultiPolygon([shapely_box(0, 0, 5, 5), shapely_box(10, 10, 15, 15)])
        xs, ys = _geometry_to_coords(mp)
        x_vals = [x for x in xs if x is not None]
        assert min(x_vals) == 0.0
        assert max(x_vals) == 15.0

    def test_empty_polygon(self):
        from shapely.geometry import Polygon
        poly = Polygon()
        xs, ys = _geometry_to_coords(poly)
        assert xs == []
        assert ys == []


# ═══════════════════════════════════════════════════════════════════════════
# Severity map
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildSeverityMap:
    def test_critical_keywords(self):
        smap = _build_severity_map(['Short', 'Open', 'Missing', 'Bridge'])
        assert all(v == 'Critical' for v in smap.values())

    def test_high_keywords(self):
        smap = _build_severity_map(['Space', 'Island', 'Pinhole'])
        assert all(v == 'High' for v in smap.values())

    def test_medium_keywords(self):
        smap = _build_severity_map(['Nick', 'Scratch', 'Dent'])
        assert all(v == 'Medium' for v in smap.values())

    def test_low_keywords(self):
        smap = _build_severity_map(['Protrusion', 'Roughness', 'Residue'])
        assert all(v == 'Low' for v in smap.values())

    def test_unknown_defaults_to_medium(self):
        smap = _build_severity_map(['UnknownDefect123'])
        assert smap['UnknownDefect123'] == 'Medium'

    def test_case_insensitive(self):
        smap = _build_severity_map(['SHORT', 'short', 'Short'])
        assert all(v == 'Critical' for v in smap.values())


# ═══════════════════════════════════════════════════════════════════════════
# Smart tick
# ═══════════════════════════════════════════════════════════════════════════

class TestSmartTick:
    def test_zero_range(self):
        assert _smart_tick(0) is None

    def test_negative_range(self):
        assert _smart_tick(-5) is None

    def test_reasonable_range(self):
        tick = _smart_tick(100)
        assert tick is not None
        assert 1 <= tick <= 50

    def test_small_range(self):
        tick = _smart_tick(1.0)
        assert tick is not None
        assert tick < 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Hover template and customdata
# ═══════════════════════════════════════════════════════════════════════════

class TestHoverHelpers:
    def test_hover_template_basic(self):
        df = pd.DataFrame({'DEFECT_TYPE': ['Short'], 'X_MM': [1.0]})
        tmpl = _build_hover_template(df)
        assert 'DEFECT_TYPE' not in tmpl or 'customdata[0]' in tmpl

    def test_customdata_shape(self):
        df = pd.DataFrame({
            'DEFECT_TYPE': ['Short', 'Nick'],
            'BUILDUP': [1, 2],
            'SIDE': ['F', 'B'],
        })
        cd = _build_customdata(df)
        assert cd.shape[0] == 2
        assert cd.shape[1] >= 1  # at least DEFECT_TYPE


# ═══════════════════════════════════════════════════════════════════════════
# Figure building (smoke tests)
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildDefectOnlyFigure:
    def test_returns_figure(self):
        df = pd.DataFrame({
            'ALIGNED_X': [10, 20],
            'ALIGNED_Y': [10, 20],
            'DEFECT_TYPE': ['Short', 'Nick'],
        })
        config = OverlayConfig(board_bounds=(0, 0, 50, 50))
        fig = build_defect_only_figure(df, config)
        assert isinstance(fig, go.Figure)

    def test_empty_df(self):
        config = OverlayConfig(board_bounds=(0, 0, 50, 50))
        fig = build_defect_only_figure(pd.DataFrame(), config)
        assert isinstance(fig, go.Figure)
