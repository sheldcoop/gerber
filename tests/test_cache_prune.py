"""Tests for prune_render_cache — age- and size-based eviction of the CAM cache.

The cache persists across server restarts (keyed by digest + CACHE_VERSION), so
pruning is the only thing bounding its growth. Eviction is whole-digest-directory.
"""
import os
import time

import core.cache as cache_mod
from core.cache import prune_render_cache


def _make_entry(root, name, size_bytes, age_days):
    """Create a fake digest dir with one file of the given size and mtime."""
    d = root / name
    d.mkdir(parents=True)
    f = d / "manifest.json"
    f.write_bytes(b"x" * size_bytes)
    mtime = time.time() - age_days * 86400
    os.utime(f, (mtime, mtime))
    return d


def test_old_entries_evicted(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    fresh = _make_entry(tmp_path, "fresh", 10, age_days=1)
    stale = _make_entry(tmp_path, "stale", 10, age_days=60)

    prune_render_cache(max_total_bytes=1024, max_age_days=30)

    assert fresh.exists()
    assert not stale.exists()


def test_size_cap_evicts_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    oldest = _make_entry(tmp_path, "oldest", 100, age_days=3)
    middle = _make_entry(tmp_path, "middle", 100, age_days=2)
    newest = _make_entry(tmp_path, "newest", 100, age_days=1)

    # Cap allows only two entries — the oldest must go, newer two stay.
    prune_render_cache(max_total_bytes=200, max_age_days=30)

    assert not oldest.exists()
    assert middle.exists()
    assert newest.exists()


def test_under_cap_keeps_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    a = _make_entry(tmp_path, "a", 10, age_days=1)
    b = _make_entry(tmp_path, "b", 10, age_days=2)

    prune_render_cache(max_total_bytes=1024, max_age_days=30)

    assert a.exists() and b.exists()


def test_missing_cache_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path / "does-not-exist")
    prune_render_cache()  # must not raise
