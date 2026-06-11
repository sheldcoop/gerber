"""Tests for the Load & Process skip predicate — the smart-button behavior.

The design phase is skipped only when the render key (archive digest + layer
selection) is unchanged AND a rendered board is already in session.
"""
from ui.sidebar import _design_unchanged


def test_same_key_with_render_skips():
    assert _design_unchanged("key1", "key1", has_rendered=True)


def test_different_key_never_skips():
    assert not _design_unchanged("key1", "key2", has_rendered=True)


def test_missing_render_never_skips():
    # Same key but the session lost the rendered object — must reload.
    assert not _design_unchanged("key1", "key1", has_rendered=False)


def test_first_load_never_skips():
    assert not _design_unchanged(None, "key1", has_rendered=True)
    assert not _design_unchanged(None, "key1", has_rendered=False)
