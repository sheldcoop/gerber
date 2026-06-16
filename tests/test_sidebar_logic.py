"""Tests for the Load & Process skip predicate — the smart-button behavior —
and the incremental-render prerendered-layer assembly (disk source).
"""
import core.cache as cache_mod
from gerber_renderer import RenderedLayer, RenderedODB
from ui.sidebar import _design_unchanged, _assemble_prerendered


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


# ── _assemble_prerendered (disk source; session source covered by e2e tests) ──

def _fake_rendered(names):
    def lyr(n):
        return RenderedLayer(
            name=n, layer_type='copper',
            svg_string=f"<svg><!-- {n} --></svg>",
            svg_data_url='data:image/svg+xml;base64,xx',
            color_svg_urls={'#ff0000': 'data:image/svg+xml;base64,yy'},
            gerber_file=None, bounds=(0.0, 0.0, 10.0, 10.0),
            feature_count=3, stats={'flash': 1, 'line': 1, 'region': 1, 'clear': 0},
        )
    return RenderedODB(layers={n: lyr(n) for n in names},
                       board_bounds=(0.0, 0.0, 10.0, 10.0))


def test_assemble_from_disk_source(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    cache_mod.save_render_cache(_fake_rendered(['4F', '3F']), digest='prevkey',
                                tgz_digest='raw', selection=['4F', '3F'])
    scanned = [('4F', 'copper'), ('3F', 'copper'), ('FSR', 'soldermask')]
    # No session render (prev_key/prev_sel None) → falls to the disk source.
    out = _assemble_prerendered('raw', ['4F', '3F', 'FSR'], scanned,
                                None, None, 'newkey')
    assert out is not None and set(out) == {'4F', '3F'}
    assert all(l.gerber_file is None for l in out.values())


def test_assemble_no_source_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    scanned = [('4F', 'copper')]
    assert _assemble_prerendered('raw', ['4F'], scanned, None, None, 'k') is None
    assert _assemble_prerendered('raw', None, scanned, None, None, 'k') is None
