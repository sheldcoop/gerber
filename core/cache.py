"""
core/cache.py — Disk cache for RenderedODB results and SVG data URL helpers.
"""

import base64
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Optional


def _svg_to_data_url_fast(svg_str: str) -> str:
    """Convert SVG string to base64 data URL (cached-friendly)."""
    b64 = base64.b64encode(svg_str.encode('utf-8')).decode('ascii')
    return f"data:image/svg+xml;base64,{b64}"


# ── Cache directory ────────────────────────────────────────────────────────────
_CAM_CACHE_DIR = Path.home() / '.cache' / 'gerber-vrs' / 'cam'


def compute_tgz_digest(tgz_bytes: bytes) -> str:
    """Return MD5 hex digest for TGZ bytes.

    Call ONCE at upload time and store the result in session state.
    Passing the returned digest to save/load avoids re-hashing the full archive
    on every Streamlit re-run.
    """
    return hashlib.md5(tgz_bytes).hexdigest()


def _cache_dir(digest: str) -> Path:
    return _CAM_CACHE_DIR / digest


# ── PanelLayout serialisation helpers ─────────────────────────────────────────

def _panel_layout_to_dict(pl) -> Optional[dict]:
    if pl is None:
        return None
    sh = {
        step: [dataclasses.asdict(sr) for sr in sr_list]
        for step, sr_list in pl.step_hierarchy.items()
    }
    return {
        'unit_positions': [list(p) for p in pl.unit_positions],
        'unit_bounds': list(pl.unit_bounds),
        'total_units': pl.total_units,
        'rows': pl.rows,
        'cols': pl.cols,
        'step_hierarchy': sh,
        'panel_width': pl.panel_width,
        'panel_height': pl.panel_height,
        'unit_positions_raw': [list(p) for p in pl.unit_positions_raw],
    }


def _panel_layout_from_dict(d: Optional[dict]):
    if d is None:
        return None
    from gerber_renderer import PanelLayout
    from odb.models import StepRepeat
    sh = {
        step: [StepRepeat(**sr) for sr in sr_list]
        for step, sr_list in d['step_hierarchy'].items()
    }
    return PanelLayout(
        unit_positions=[tuple(p) for p in d['unit_positions']],
        unit_bounds=tuple(d['unit_bounds']),
        total_units=d['total_units'],
        rows=d['rows'],
        cols=d['cols'],
        step_hierarchy=sh,
        panel_width=d['panel_width'],
        panel_height=d['panel_height'],
        unit_positions_raw=[tuple(p) for p in d['unit_positions_raw']],
    )


def save_render_cache(rendered, *, digest: str = None, tgz_bytes: bytes = None) -> None:
    """Persist a RenderedODB to disk under ~/.cache/gerber-vrs/cam/{digest}/.

    Pass ``digest`` (pre-computed via compute_tgz_digest) to avoid re-hashing.
    Falls back to computing from ``tgz_bytes`` when ``digest`` is omitted.
    """
    if digest is None:
        if tgz_bytes is None:
            return
        digest = compute_tgz_digest(tgz_bytes)
    try:
        cache_dir = _cache_dir(digest)
        cache_dir.mkdir(parents=True, exist_ok=True)

        layer_meta = {}
        for name, lyr in rendered.layers.items():
            # Write SVG to its own file (keeps manifest.json small)
            (cache_dir / f"{name}.svg").write_text(lyr.svg_string, encoding='utf-8')

            # Decode panel data URL → raw SVG so it stores compactly
            if lyr.panel_svg_data_url:
                try:
                    _b64 = lyr.panel_svg_data_url.split(',', 1)[1]
                    _panel_svg = base64.b64decode(_b64).decode('utf-8')
                    (cache_dir / f"{name}.panel.svg").write_text(_panel_svg, encoding='utf-8')
                except Exception:
                    pass

            stack_color = next(iter(lyr.color_svg_urls), None)
            layer_meta[name] = {
                'layer_type': lyr.layer_type,
                'bounds': list(lyr.bounds),
                'feature_count': lyr.feature_count,
                'stats': lyr.stats,
                'fg_color': '#FFD700' if lyr.layer_type == 'drill' else '#b87333',
                'stack_color': stack_color,
            }

        manifest = {
            'board_bounds': list(rendered.board_bounds),
            'step_name': rendered.step_name,
            'units': rendered.units,
            'warnings': rendered.warnings,
            'panel_layout': _panel_layout_to_dict(rendered.panel_layout),
            'layers': layer_meta,
        }
        (cache_dir / 'manifest.json').write_text(
            json.dumps(manifest, separators=(',', ':')), encoding='utf-8'
        )
    except Exception:
        pass  # cache write failure is non-fatal


def get_cache_size() -> tuple:
    """Return (total_bytes, human_readable_string) for the CAM cache directory.
    
    Returns:
        (total_bytes: int, formatted_size: str)
        Example: (10485760, "10.0 MB")
    """
    total = 0
    if _CAM_CACHE_DIR.exists():
        for item in _CAM_CACHE_DIR.rglob('*'):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except (OSError, PermissionError):
                    pass
    
    # Format size
    if total == 0:
        return (0, "0 B")
    elif total < 1024:
        return (total, f"{total} B")
    elif total < 1024 * 1024:
        return (total, f"{total / 1024:.1f} KB")
    elif total < 1024 * 1024 * 1024:
        return (total, f"{total / (1024 * 1024):.1f} MB")
    else:
        return (total, f"{total / (1024 * 1024 * 1024):.2f} GB")


def load_render_cache(*, digest: str = None, tgz_bytes: bytes = None) -> Optional[object]:
    """Return a cached RenderedODB, or None on cache miss.

    Pass ``digest`` (pre-computed) to avoid re-hashing the TGZ bytes.
    """
    if digest is None:
        if tgz_bytes is None:
            return None
        digest = compute_tgz_digest(tgz_bytes)
    try:
        cache_dir = _cache_dir(digest)
        manifest_path = cache_dir / 'manifest.json'
        if not manifest_path.exists():
            return None

        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        from gerber_renderer import RenderedLayer, RenderedODB

        layers = {}
        for name, meta in manifest['layers'].items():
            svg_path = cache_dir / f"{name}.svg"
            if not svg_path.exists():
                return None  # partial cache — force full re-render
            svg_string = svg_path.read_text(encoding='utf-8')
            svg_data_url = _svg_to_data_url_fast(svg_string)

            # Reconstruct stack color variant via string replace (no to_svg() call)
            fg_color = meta.get('fg_color', '#b87333')
            stack_color = meta.get('stack_color') or fg_color
            stack_svg = svg_string.replace(fg_color, stack_color) if fg_color != stack_color else svg_string
            color_svg_urls = {stack_color: _svg_to_data_url_fast(stack_svg)}

            panel_svg_data_url = ''
            panel_svg_path = cache_dir / f"{name}.panel.svg"
            if panel_svg_path.exists():
                try:
                    panel_svg_data_url = _svg_to_data_url_fast(
                        panel_svg_path.read_text(encoding='utf-8')
                    )
                except Exception:
                    pass

            layers[name] = RenderedLayer(
                name=name,
                layer_type=meta['layer_type'],
                svg_string=svg_string,
                svg_data_url=svg_data_url,
                color_svg_urls=color_svg_urls,
                gerber_file=None,
                bounds=tuple(meta['bounds']),
                feature_count=meta['feature_count'],
                panel_svg_data_url=panel_svg_data_url,
                stats=meta['stats'],
            )

        return RenderedODB(
            layers=layers,
            board_bounds=tuple(manifest['board_bounds']),
            step_name=manifest.get('step_name', ''),
            units=manifest.get('units', ''),
            panel_layout=_panel_layout_from_dict(manifest.get('panel_layout')),
            warnings=manifest.get('warnings', []),
        )
    except Exception:
        return None
