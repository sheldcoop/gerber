"""End-to-end equality: incremental rendering must produce byte-identical
per-layer SVGs and identical board geometry to a fresh full render.

Uses the real archive fhr0010_bkm.tgz (repo root), skipped when absent —
same convention as tests/test_commonality.py. Comparisons deliberately
exclude warnings, color_svg_urls and panel_svg_data_url: stack-color
assignment and first-copper choice are as_completed-order dependent and
nondeterministic across identical fresh renders today.
"""
import dataclasses
from pathlib import Path

import pytest

from core.pipeline import _render_pipeline

_TGZ = Path(__file__).resolve().parent.parent / "fhr0010_bkm.tgz"

COPPERS = ['3F', '2F']
DRILL = '2F-3F'


@pytest.fixture(scope="module")
def tgz_bytes():
    if not _TGZ.exists():
        pytest.skip("fhr0010_bkm.tgz not present")
    return _TGZ.read_bytes()


def _reuse_from(rendered, names):
    """Prerendered dict exactly as the sidebar builds it: replace-copies with
    gerber_file=None (Phase 5 must never re-clip a reused layer)."""
    return {n: dataclasses.replace(rendered.layers[n], gerber_file=None)
            for n in names}


@pytest.fixture(scope="module")
def r_coppers(tgz_bytes):
    return _render_pipeline(tgz_bytes, _TGZ.name, list(COPPERS))


@pytest.fixture(scope="module")
def r_full_fresh(tgz_bytes):
    return _render_pipeline(tgz_bytes, _TGZ.name, COPPERS + [DRILL])


@pytest.fixture(scope="module")
def r_full_incremental(tgz_bytes, r_coppers):
    # Same copper set → drill clipping context unchanged → coppers reusable.
    return _render_pipeline(tgz_bytes, _TGZ.name, COPPERS + [DRILL],
                            prerendered_layers=_reuse_from(r_coppers, COPPERS))


def test_layer_sets_match(r_full_fresh, r_full_incremental):
    assert set(r_full_incremental.layers) == set(r_full_fresh.layers)


def test_reused_copper_svgs_byte_identical(r_full_fresh, r_full_incremental):
    # Pins both selection-independence and to_svg determinism.
    for n in COPPERS:
        assert r_full_incremental.layers[n].svg_string == r_full_fresh.layers[n].svg_string
        assert r_full_incremental.layers[n].bounds == r_full_fresh.layers[n].bounds


def test_fresh_drill_matches(r_full_fresh, r_full_incremental):
    assert r_full_incremental.layers[DRILL].svg_string == r_full_fresh.layers[DRILL].svg_string
    assert r_full_incremental.layers[DRILL].bounds == r_full_fresh.layers[DRILL].bounds


def test_board_geometry_matches(r_full_fresh, r_full_incremental):
    assert r_full_incremental.board_bounds == r_full_fresh.board_bounds
    assert r_full_incremental.panel_layout.total_units == r_full_fresh.panel_layout.total_units
    assert r_full_incremental.panel_layout.unit_bounds == r_full_fresh.panel_layout.unit_bounds


def test_drill_invalidation_rerenders_correctly(tgz_bytes, r_full_fresh):
    """Copper set changed ({3F} → {3F,2F}): the planner re-renders the drill.

    The incremental result (reusing only 3F from a {3F, drill} render) must
    byte-match a fresh render of the full selection — including the drill
    re-clipped against the wider copper-derived board bounds.
    """
    r_small = _render_pipeline(tgz_bytes, _TGZ.name, ['3F', DRILL])
    r_inc = _render_pipeline(tgz_bytes, _TGZ.name, COPPERS + [DRILL],
                             prerendered_layers=_reuse_from(r_small, ['3F']))
    assert set(r_inc.layers) == set(r_full_fresh.layers)
    for n in COPPERS + [DRILL]:
        assert r_inc.layers[n].svg_string == r_full_fresh.layers[n].svg_string
    assert r_inc.board_bounds == r_full_fresh.board_bounds
