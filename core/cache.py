"""
core/cache.py — Disk cache for RenderedODB results and SVG data URL helpers.
"""

import base64
import dataclasses
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from core.svg_utils import build_stack_svg
from core.constants import CAM_CACHE_DIR as _CAM_CACHE_DIR, COPPER_FG, DRILL_FG, layer_fg

logger = logging.getLogger(__name__)


def _svg_to_data_url_fast(svg_str: str) -> str:
    """Convert SVG string to base64 data URL (cached-friendly)."""
    b64 = base64.b64encode(svg_str.encode('utf-8')).decode('ascii')
    return f"data:image/svg+xml;base64,{b64}"


def compute_tgz_digest(tgz_bytes: bytes) -> str:
    """Return MD5 hex digest for TGZ bytes.

    Call ONCE at upload time and store the result in session state.
    Passing the returned digest to save/load avoids re-hashing the full archive
    on every Streamlit re-run.
    """
    return hashlib.md5(tgz_bytes).hexdigest()


# Bump whenever render OUTPUT changes (SVG generation, RenderedLayer fields,
# manifest schema, build_stack_svg recolouring). The version is folded into every
# render key, so old on-disk entries become unreachable — never wrongly served —
# and age out via prune_render_cache().
# v2: manifest gains 'tgz_digest' + 'selection' (incremental layer reuse).
CACHE_VERSION = 2


def compose_render_key(digest: str, layer_filter, cache_version: int = CACHE_VERSION) -> str:
    """Cache key for a render, folding in the selected layer set and cache version.

    The render cache is keyed by this string, so two different layer selections of
    the same archive cache independently, and a CACHE_VERSION bump invalidates all
    entries written by older render code. Order-independent and case-insensitive
    in the layer names; None and [] (render everything) produce the same key.
    """
    sig = ",".join(sorted(n.lower() for n in layer_filter)) if layer_filter else ""
    return hashlib.md5(f"{digest}|v{cache_version}|{sig}".encode()).hexdigest()


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
        'dominant_angle': getattr(pl, 'dominant_angle', 0.0),
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
        dominant_angle=d.get('dominant_angle', 0.0),
    )


def save_render_cache(rendered, *, digest: str = None, tgz_bytes: bytes = None,
                      tgz_digest: str = None, selection: list = None) -> None:
    """Persist a RenderedODB to disk under ~/.cache/gerber-vrs/cam/{digest}/.

    Pass ``digest`` (pre-computed via compute_tgz_digest) to avoid re-hashing.
    Falls back to computing from ``tgz_bytes`` when ``digest`` is omitted.

    ``tgz_digest`` (raw archive MD5) and ``selection`` (rendered layer names,
    None = all) make the entry discoverable as an incremental-reuse source.
    When omitted on a re-save, the existing manifest's values carry forward.
    """
    if digest is None:
        if tgz_bytes is None:
            return
        digest = compute_tgz_digest(tgz_bytes)
    try:
        cache_dir = _cache_dir(digest)
        cache_dir.mkdir(parents=True, exist_ok=True)

        if tgz_digest is None or selection is None:
            # Re-save without reuse metadata (e.g. after a lazy panel-SVG build):
            # carry the existing manifest's fields forward so reuse keeps working.
            try:
                _prev = json.loads((cache_dir / 'manifest.json').read_text(encoding='utf-8'))
                if tgz_digest is None:
                    tgz_digest = _prev.get('tgz_digest')
                if selection is None:
                    selection = _prev.get('selection')
            except Exception:
                pass

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
                'fg_color': layer_fg(lyr.layer_type),
                'stack_color': stack_color,
            }

        manifest = {
            'board_bounds': list(rendered.board_bounds),
            'step_name': rendered.step_name,
            'units': rendered.units,
            'warnings': rendered.warnings,
            'panel_layout': _panel_layout_to_dict(rendered.panel_layout),
            'layers': layer_meta,
            # Incremental-reuse metadata (None-safe; loaders tolerate absence).
            'tgz_digest': tgz_digest,
            'selection': sorted(n.lower() for n in selection) if selection else None,
        }
        (cache_dir / 'manifest.json').write_text(
            json.dumps(manifest, separators=(',', ':')), encoding='utf-8'
        )
    except Exception:
        # Non-fatal: the render still works this session, it just won't persist.
        logger.warning("Render cache write failed for digest %s", digest, exc_info=True)


def prune_render_cache(max_total_bytes: int = 2 * 1024**3, max_age_days: int = 30) -> None:
    """Bound the persistent CAM cache: drop entries untouched for ``max_age_days``,
    then evict oldest-first until total size is under ``max_total_bytes``.

    Runs once per server start (app.py). Eviction is whole-digest-directory, so a
    pruned entry simply re-renders on next load; the sidebar's "Clear All Cache"
    button remains the manual full wipe.
    """
    import shutil
    import time
    try:
        if not _CAM_CACHE_DIR.exists():
            return
        now = time.time()
        entries = []  # (newest_mtime, total_size, dir_path)
        for d in _CAM_CACHE_DIR.iterdir():
            if not d.is_dir():
                continue
            try:
                files = [f for f in d.rglob('*') if f.is_file()]
                size = sum(f.stat().st_size for f in files)
                mtime = max((f.stat().st_mtime for f in files), default=d.stat().st_mtime)
            except (OSError, PermissionError):
                continue
            entries.append((mtime, size, d))

        kept = []
        for mtime, size, d in entries:
            if now - mtime > max_age_days * 86400:
                shutil.rmtree(d, ignore_errors=True)
            else:
                kept.append((mtime, size, d))

        total = sum(size for _, size, _ in kept)
        for mtime, size, d in sorted(kept, key=lambda e: e[0]):  # oldest first
            if total <= max_total_bytes:
                break
            shutil.rmtree(d, ignore_errors=True)
            total -= size
    except Exception:
        logger.warning("Render cache prune failed", exc_info=True)


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


def _layer_from_cache(cache_dir: Path, name: str, meta: dict) -> Optional[object]:
    """Rebuild one RenderedLayer from its cached SVG + manifest meta.

    Returns None when the SVG file is missing (partial cache → caller decides).
    """
    from gerber_renderer import RenderedLayer

    svg_path = cache_dir / f"{name}.svg"
    if not svg_path.exists():
        return None
    svg_string = svg_path.read_text(encoding='utf-8')
    svg_data_url = _svg_to_data_url_fast(svg_string)

    # Reconstruct stack color variant (recolour fg + transparent bg, no to_svg() call)
    fg_color = meta.get('fg_color', COPPER_FG)
    stack_color = meta.get('stack_color') or fg_color
    stack_svg = build_stack_svg(svg_string, fg_color, stack_color)
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

    return RenderedLayer(
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
        from gerber_renderer import RenderedODB

        layers = {}
        for name, meta in manifest['layers'].items():
            layer = _layer_from_cache(cache_dir, name, meta)
            if layer is None:
                return None  # partial cache — force full re-render
            layers[name] = layer

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


# ── Incremental-reuse helpers ─────────────────────────────────────────────────

def load_render_manifest(render_key: str) -> Optional[dict]:
    """Read just the manifest (KBs) for a cached render, or None."""
    try:
        return json.loads(
            (_cache_dir(render_key) / 'manifest.json').read_text(encoding='utf-8')
        )
    except Exception:
        return None


def load_cached_layers(render_key: str, names) -> Optional[dict]:
    """Partial load: rebuild only the named RenderedLayers from a cache entry.

    Returns {name: RenderedLayer} (gerber_file=None), or None if the manifest
    or any requested SVG is missing — callers fall back to a full render.
    """
    manifest = load_render_manifest(render_key)
    if manifest is None:
        return None
    try:
        cache_dir = _cache_dir(render_key)
        meta_by_lower = {n.lower(): (n, m) for n, m in manifest['layers'].items()}
        out = {}
        for want in names:
            hit = meta_by_lower.get(str(want).lower())
            if hit is None:
                return None
            real_name, meta = hit
            layer = _layer_from_cache(cache_dir, real_name, meta)
            if layer is None:
                return None
            out[real_name] = layer
        return out
    except Exception:
        return None


def find_reuse_source(tgz_digest: str, exclude_key: str = None) -> Optional[tuple]:
    """Find the best same-archive cache entry to reuse layers from.

    Scans every manifest under the CAM cache dir for one whose 'tgz_digest'
    matches; among matches, prefers the one with the most rendered layers.
    Returns (render_key, manifest) or None. Manifests without the reuse
    fields (pre-v2 or foreign) are skipped.
    """
    if not tgz_digest or not _CAM_CACHE_DIR.exists():
        return None
    best = None
    try:
        for d in _CAM_CACHE_DIR.iterdir():
            if not d.is_dir() or d.name == exclude_key:
                continue
            manifest = load_render_manifest(d.name)
            if not manifest or manifest.get('tgz_digest') != tgz_digest:
                continue
            n_layers = len(manifest.get('layers', {}))
            if best is None or n_layers > best[2]:
                best = (d.name, manifest, n_layers)
    except Exception:
        return None
    return (best[0], best[1]) if best else None
