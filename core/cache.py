"""
core/cache.py — Disk cache for RenderedODB results and SVG data URL helpers.
"""

import base64
import hashlib
import pickle
from pathlib import Path
from typing import Optional


def _svg_to_data_url_fast(svg_str: str) -> str:
    """Convert SVG string to base64 data URL (cached-friendly)."""
    b64 = base64.b64encode(svg_str.encode('utf-8')).decode('ascii')
    return f"data:image/svg+xml;base64,{b64}"


# ── Disk cache ────────────────────────────────────────────────────────────────
_CAM_CACHE_DIR = Path.home() / '.cache' / 'gerber-vrs' / 'cam'


def _tgz_cache_path(tgz_bytes: bytes) -> Path:
    digest = hashlib.md5(tgz_bytes).hexdigest()
    return _CAM_CACHE_DIR / f"{digest}.pkl"


def save_render_cache(tgz_bytes: bytes, rendered) -> None:
    """Persist a RenderedODB to disk, keyed by MD5 of the TGZ content."""
    try:
        _CAM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            'layers': {
                name: {
                    'name': lyr.name,
                    'layer_type': lyr.layer_type,
                    'svg_string': lyr.svg_string,
                    'svg_data_url': lyr.svg_data_url,
                    'color_svg_urls': lyr.color_svg_urls,
                    'bounds': lyr.bounds,
                    'feature_count': lyr.feature_count,
                    'panel_svg_data_url': lyr.panel_svg_data_url,
                    'stats': lyr.stats,
                }
                for name, lyr in rendered.layers.items()
            },
            'board_bounds': rendered.board_bounds,
            'step_name': rendered.step_name,
            'units': rendered.units,
            'panel_layout': rendered.panel_layout,
            'warnings': rendered.warnings,
        }
        cache_path = _tgz_cache_path(tgz_bytes)
        with open(cache_path, 'wb') as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass  # cache write failure is non-fatal


def load_render_cache(tgz_bytes: bytes) -> Optional[object]:
    """Return a cached RenderedODB for this TGZ, or None if not cached."""
    try:
        cache_path = _tgz_cache_path(tgz_bytes)
        if not cache_path.exists():
            return None
        with open(cache_path, 'rb') as f:
            payload = pickle.load(f)
        # Lazy import to avoid circular dependency
        from gerber_renderer import RenderedLayer, RenderedODB
        layers = {}
        for name, d in payload['layers'].items():
            layers[name] = RenderedLayer(
                name=d['name'],
                layer_type=d['layer_type'],
                svg_string=d['svg_string'],
                svg_data_url=d['svg_data_url'],
                color_svg_urls=d['color_svg_urls'],
                gerber_file=None,
                bounds=d['bounds'],
                feature_count=d['feature_count'],
                panel_svg_data_url=d.get('panel_svg_data_url', ''),
                stats=d['stats'],
            )
        return RenderedODB(
            layers=layers,
            board_bounds=payload['board_bounds'],
            step_name=payload.get('step_name', ''),
            units=payload.get('units', ''),
            panel_layout=payload['panel_layout'],
            warnings=payload.get('warnings', []),
        )
    except Exception:
        return None
