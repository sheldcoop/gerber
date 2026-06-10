"""Tests for compose_render_key — the per-selection render cache key.

Guarantees: None and [] (render everything) map to the same key, the key is
order-independent, different selections produce different keys, and bumping
CACHE_VERSION invalidates every previously written key.
"""
from core.cache import compose_render_key

_DIGEST = "abc123def456"


def test_none_and_empty_equivalent():
    # All-layers render: None and [] must agree; the raw digest is never the key
    # (CACHE_VERSION is always folded in so version bumps invalidate everything).
    assert compose_render_key(_DIGEST, None) == compose_render_key(_DIGEST, [])
    assert compose_render_key(_DIGEST, None) != _DIGEST


def test_version_invalidates():
    assert compose_render_key(_DIGEST, None, cache_version=1) != \
           compose_render_key(_DIGEST, None, cache_version=2)
    assert compose_render_key(_DIGEST, ["4F"], cache_version=1) != \
           compose_render_key(_DIGEST, ["4F"], cache_version=2)


def test_order_independent():
    a = compose_render_key(_DIGEST, ["4F", "2B", "SMT"])
    b = compose_render_key(_DIGEST, ["SMT", "2B", "4F"])
    assert a == b


def test_case_insensitive():
    assert compose_render_key(_DIGEST, ["4F", "2B"]) == \
           compose_render_key(_DIGEST, ["4f", "2b"])


def test_different_selections_differ():
    k_two = compose_render_key(_DIGEST, ["4F", "2B"])
    k_one = compose_render_key(_DIGEST, ["4F"])
    assert k_two != k_one
    # A subset must also differ from the all-layers (plain digest) key.
    assert k_two != _DIGEST


def test_different_digests_differ_for_same_selection():
    sel = ["4F", "2B"]
    assert compose_render_key("digestA", sel) != compose_render_key("digestB", sel)
