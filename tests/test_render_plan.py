"""Tests for core/render_plan.py — the incremental-render reuse planner.

Pins the safety rules: copper/soldermask reuse freely; drill reuse only when
the copper-set signature is unchanged (drill clipping depends on it); anything
unknown or missing re-renders; render-all (None) does no reuse.
"""
from core.render_plan import (
    effective_layer_type, copper_set_signature, plan_layer_reuse,
)

# A typical scanned-layer type map (names as the ODB++ matrix reports them).
TYPES = {
    '4F': 'copper',
    '3F': 'copper',
    'FSR': 'soldermask',
    '2B-3B': 'mixed',    # drill span mislabelled by the matrix
    'D1-2': 'drill',
}


# ── effective_layer_type ─────────────────────────────────────────────────────

def test_drill_span_reclassified():
    assert effective_layer_type('2B-3B', 'mixed') == 'drill'
    assert effective_layer_type('2F_3F', 'signal') == 'drill'


def test_plain_copper_unchanged():
    assert effective_layer_type('4F', 'copper') == 'copper'


def test_already_drill_unchanged():
    assert effective_layer_type('D1-2', 'drill') == 'drill'


# ── copper_set_signature ─────────────────────────────────────────────────────

def test_signature_excludes_drills_and_soldermask():
    sig = copper_set_signature(['4F', 'FSR', '2B-3B', 'D1-2'], TYPES)
    assert sig == ('4f',)


def test_signature_sorted_and_lowercase():
    assert copper_set_signature(['4F', '3F'], TYPES) == \
           copper_set_signature(['3f', '4f'], TYPES) == ('3f', '4f')


def test_signature_empty_selection():
    assert copper_set_signature(None, TYPES) == ()
    assert copper_set_signature([], TYPES) == ()


# ── plan_layer_reuse ─────────────────────────────────────────────────────────

def test_add_drill_reuses_everything_else():
    plan = plan_layer_reuse(['4F', 'FSR'], ['4F', 'FSR'],
                            ['4F', 'FSR', 'D1-2'], TYPES)
    assert plan.reusable == ('4F', 'FSR')
    assert plan.to_render == ('D1-2',)
    assert plan.drill_invalidated is False


def test_add_copper_invalidates_drill():
    # Copper set grows → board bounds may change → drill clip invalid.
    plan = plan_layer_reuse(['4F', 'D1-2'], ['4F', 'D1-2'],
                            ['4F', '3F', 'D1-2'], TYPES)
    assert plan.reusable == ('4F',)
    assert set(plan.to_render) == {'3F', 'D1-2'}
    assert plan.drill_invalidated is True


def test_remove_copper_invalidates_drill():
    plan = plan_layer_reuse(['4F', '3F', 'D1-2'], ['4F', '3F', 'D1-2'],
                            ['4F', 'D1-2'], TYPES)
    assert plan.reusable == ('4F',)
    assert plan.to_render == ('D1-2',)
    assert plan.drill_invalidated is True


def test_drill_span_treated_as_drill():
    # '2B-3B' is matrix-mixed but effectively drill: copper change re-renders it.
    plan = plan_layer_reuse(['4F', '2B-3B'], ['4F', '2B-3B'],
                            ['4F', '3F', '2B-3B'], TYPES)
    assert '2B-3B' in plan.to_render


def test_add_soldermask_reuses_everything():
    plan = plan_layer_reuse(['4F'], ['4F'], ['4F', 'FSR'], TYPES)
    assert plan.reusable == ('4F',)
    assert plan.to_render == ('FSR',)
    assert plan.drill_invalidated is False


def test_render_all_means_no_reuse():
    assert plan_layer_reuse(None, ['4F'], ['4F'], TYPES).reusable == ()
    plan = plan_layer_reuse(['4F'], ['4F'], None, TYPES)
    assert plan.reusable == () and plan.to_render == ()


def test_missing_from_prev_render_is_rerendered():
    # e.g. a drill layer the Phase-5 clip deleted, or a parse failure.
    plan = plan_layer_reuse(['4F', 'D1-2'], ['4F'],   # D1-2 not actually rendered
                            ['4F', 'D1-2'], TYPES)
    assert plan.reusable == ('4F',)
    assert plan.to_render == ('D1-2',)


def test_case_insensitive_matching():
    plan = plan_layer_reuse(['4f'], ['4F'], ['4F'], {'4f': 'copper'})
    assert plan.reusable == ('4F',)


def test_unknown_type_is_rerendered():
    plan = plan_layer_reuse(['mystery'], ['mystery'], ['mystery'], {})
    assert plan.to_render == ('mystery',)
