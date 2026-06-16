"""Tests for the incremental-reuse cache helpers (core/cache.py v2 manifest).

Covers: tgz_digest/selection roundtrip, carry-forward on a kwarg-less re-save,
partial layer loading, reuse-source discovery, and tolerance of pre-v2
manifests that lack the new fields.
"""
import json

import core.cache as cache_mod
from core.cache import (
    save_render_cache, load_render_cache, load_render_manifest,
    load_cached_layers, find_reuse_source,
)
from gerber_renderer import RenderedLayer, RenderedODB


def _fake_layer(name, layer_type='copper'):
    svg = f"<svg><!-- {name} --></svg>"
    return RenderedLayer(
        name=name, layer_type=layer_type,
        svg_string=svg, svg_data_url='data:image/svg+xml;base64,xx',
        color_svg_urls={'#ff0000': 'data:image/svg+xml;base64,yy'},
        gerber_file=None, bounds=(0.0, 0.0, 10.0, 10.0),
        feature_count=3, stats={'flash': 1, 'line': 1, 'region': 1, 'clear': 0},
    )


def _fake_rendered(names):
    return RenderedODB(
        layers={n: _fake_layer(n) for n in names},
        board_bounds=(0.0, 0.0, 10.0, 10.0),
        step_name='unit', units='mm',
    )


def test_reuse_fields_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    save_render_cache(_fake_rendered(['4F']), digest='key1',
                      tgz_digest='rawdigest', selection=['4F'])
    m = load_render_manifest('key1')
    assert m['tgz_digest'] == 'rawdigest'
    assert m['selection'] == ['4f']  # stored sorted + lowercase


def test_resave_carries_fields_forward(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    save_render_cache(_fake_rendered(['4F']), digest='key1',
                      tgz_digest='rawdigest', selection=['4F'])
    # Re-save WITHOUT the kwargs (e.g. after a lazy panel-SVG build).
    save_render_cache(_fake_rendered(['4F']), digest='key1')
    m = load_render_manifest('key1')
    assert m['tgz_digest'] == 'rawdigest'
    assert m['selection'] == ['4f']


def test_load_cached_layers_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    save_render_cache(_fake_rendered(['4F', '3F']), digest='key1',
                      tgz_digest='raw', selection=['4F', '3F'])
    out = load_cached_layers('key1', ['4f'])  # case-insensitive request
    assert out is not None and set(out) == {'4F'}
    assert out['4F'].svg_string == "<svg><!-- 4F --></svg>"
    assert out['4F'].gerber_file is None


def test_load_cached_layers_missing_svg_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    save_render_cache(_fake_rendered(['4F']), digest='key1',
                      tgz_digest='raw', selection=['4F'])
    (tmp_path / 'key1' / '4F.svg').unlink()
    assert load_cached_layers('key1', ['4F']) is None
    assert load_cached_layers('key1', ['unknown']) is None


def test_find_reuse_source_prefers_most_layers(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    save_render_cache(_fake_rendered(['4F']), digest='small',
                      tgz_digest='raw', selection=['4F'])
    save_render_cache(_fake_rendered(['4F', '3F']), digest='big',
                      tgz_digest='raw', selection=['4F', '3F'])
    save_render_cache(_fake_rendered(['4F']), digest='other',
                      tgz_digest='DIFFERENT', selection=['4F'])
    key, manifest = find_reuse_source('raw')
    assert key == 'big'
    assert find_reuse_source('raw', exclude_key='big')[0] == 'small'
    assert find_reuse_source('nomatch') is None


def test_pre_v2_manifest_still_loads_and_is_skipped_for_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "_CAM_CACHE_DIR", tmp_path)
    save_render_cache(_fake_rendered(['4F']), digest='old',
                      tgz_digest='raw', selection=['4F'])
    # Strip the v2 fields to simulate a pre-v2 manifest.
    mp = tmp_path / 'old' / 'manifest.json'
    m = json.loads(mp.read_text())
    del m['tgz_digest'], m['selection']
    mp.write_text(json.dumps(m))

    assert load_render_cache(digest='old') is not None     # full load still fine
    assert find_reuse_source('raw') is None                # but never a reuse source
