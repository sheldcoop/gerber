"""Tests for compose_render_key — the per-selection render cache key.

Guarantees: rendering "everything" reuses the plain digest (backward compatible),
the key is order-independent, and different selections produce different keys.
"""
from core.cache import compose_render_key

_DIGEST = "abc123def456"


def test_none_or_empty_returns_plain_digest():
    # All-layers render must keep the existing digest so old caches still hit.
    assert compose_render_key(_DIGEST, None) == _DIGEST
    assert compose_render_key(_DIGEST, []) == _DIGEST


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
