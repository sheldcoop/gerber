"""
gerber_renderer.py — ODB++ to Gerbonara CAM-quality SVG renderer.

Parses ODB++ features → Gerbonara Flash/Line/Region objects → SVG.
Each pad, trace, and surface retains its identity for CAM-quality rendering.

Performance: layers are parsed in parallel via ThreadPoolExecutor.
SVGs and data URLs are pre-cached to avoid re-rendering on UI interactions.
"""

import base64
import hashlib
import math
import os
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from gerbonara import GerberFile
from gerbonara.graphic_objects import Flash, Line, Region
from gerbonara.apertures import (
    CircleAperture, RectangleAperture, ObroundAperture,
)
from gerbonara.utils import MM

from odb_parser import (
    _extract_odb_tgz,
    _read_units,
    _parse_matrix,
    _scan_layers_dir,
    _find_step,
    _read_features_text,
    _parse_symbol_table,
    _load_user_symbols,
    _detect_symbol_scale,
    _units_from_text,
    _ODBSymbol,
    _odb_arc_to_points,
    _parse_step_repeat,
    StepRepeat,
)


def _svg_to_data_url_fast(svg_str: str) -> str:
    """Convert SVG string to base64 data URL (cached-friendly)."""
    b64 = base64.b64encode(svg_str.encode('utf-8')).decode('ascii')
    return f"data:image/svg+xml;base64,{b64}"


# ── Disk cache ────────────────────────────────────────────────────────────────
_CAM_CACHE_DIR = Path.home() / '.cache' / 'gerber-vrs' / 'cam'


def _tgz_cache_path(tgz_bytes: bytes) -> Path:
    digest = hashlib.md5(tgz_bytes).hexdigest()
    return _CAM_CACHE_DIR / f"{digest}.pkl"


def save_render_cache(tgz_bytes: bytes, rendered: 'RenderedODB') -> None:
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


def load_render_cache(tgz_bytes: bytes) -> Optional['RenderedODB']:
    """Return a cached RenderedODB for this TGZ, or None if not cached."""
    try:
        cache_path = _tgz_cache_path(tgz_bytes)
        if not cache_path.exists():
            return None
        with open(cache_path, 'rb') as f:
            payload = pickle.load(f)
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


def build_panel_svg(svg_string: str, panel_layout) -> str:
    """Composite unit SVG into a panel SVG using <use> tiling.

    Defines the unit artwork once in <defs> and references it N times with
    translate transforms.  Result is ~50 KB vs ~5 MB for a raster PNG and
    renders at any zoom without pixelation.

    Args:
        svg_string: SVG string for one unit (from gerbonara render).
        panel_layout: PanelLayout with unit_positions and panel_width/height.

    Returns:
        Base64 SVG data URL, or '' on failure.
    """
    import re as _re_local

    # Extract viewBox from unit SVG to get its coordinate space
    vb_match = _re_local.search(r'viewBox=["\']([^"\']+)["\']', svg_string)
    if not vb_match:
        return ''
    vb_parts = vb_match.group(1).split()
    if len(vb_parts) != 4:
        return ''
    vx, vy, vw, vh = map(float, vb_parts)
    if vw <= 0 or vh <= 0:
        return ''

    # Extract inner SVG content (everything between the root <svg> tags)
    inner_match = _re_local.search(r'<svg[^>]*>(.*?)</svg>', svg_string, _re_local.DOTALL)
    if not inner_match:
        return ''
    inner = inner_match.group(1).strip()

    pw, ph = panel_layout.panel_width, panel_layout.panel_height
    uw, uh = panel_layout.unit_bounds

    # Build <use> elements for each panel position.
    # unit_positions give bottom-left corner in mm (Y=0 at bottom).
    # SVG Y=0 is at top, so we flip: svg_y = ph - (y_mm + uh).
    # Within the unit coordinate space the origin matches the viewBox origin,
    # so translate = (x_mm - vx,  (ph - y_mm - uh) - vy).
    uses = []
    for x_mm, y_mm in panel_layout.unit_positions:
        tx = x_mm - vx
        ty = (ph - y_mm - uh) - vy
        uses.append(
            f'<use href="#_u" xlink:href="#_u" transform="translate({tx:.4f} {ty:.4f})"/>'
        )

    composite = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {pw:.4f} {ph:.4f}">'
        f'<rect width="{pw:.4f}" height="{ph:.4f}" fill="#060A06"/>'
        f'<defs><g id="_u">{inner}</g></defs>'
        f'{"".join(uses)}'
        f'</svg>'
    )
    return _svg_to_data_url_fast(composite)


# Pre-defined color palette for stacking
LAYER_COLORS = ['#b87333', '#4488cc', '#44aa44', '#9966bb', '#cc6644',
                '#44ccaa', '#cc4466', '#44cccc']


@dataclass
class RenderedLayer:
    """A single rendered copper layer with pre-cached SVG variants."""
    name: str
    layer_type: str
    svg_string: str              # default copper color SVG
    svg_data_url: str            # pre-encoded base64 data URL (default color)
    color_svg_urls: dict         # {color_hex: data_url} for stacking
    gerber_file: GerberFile
    bounds: tuple                # (min_x, min_y, max_x, max_y) in mm
    feature_count: int
    panel_svg_data_url: str = '' # pre-rendered panel tile SVG (composite SVG data url)
    stats: dict = field(default_factory=dict)


@dataclass
class PanelLayout:
    """Panel tiling layout derived from TGZ STEP-REPEAT data."""
    unit_positions: list          # [(x_mm, y_mm), ...] centered for panel SVG display
    unit_bounds: tuple            # (width_mm, height_mm) of a single unit
    total_units: int
    rows: int                     # effective total rows across entire panel
    cols: int                     # effective total cols across entire panel
    step_hierarchy: dict          # raw step-repeat data {step: [StepRepeat, ...]}
    panel_width: float = 510.0    # mm (constant)
    panel_height: float = 515.0   # mm (constant)
    unit_positions_raw: list = field(default_factory=list)  # raw ODB++ coords (pre-centering)


@dataclass
class RenderedODB:
    """All rendered layers from an ODB++ archive."""
    layers: dict  # name → RenderedLayer
    board_bounds: tuple  # aggregate (min_x, min_y, max_x, max_y) in mm
    step_name: str = ''
    units: str = ''
    panel_layout: Optional[PanelLayout] = None
    warnings: list = field(default_factory=list)


def _make_aperture(sym: _ODBSymbol):
    """Map ODB++ symbol to Gerbonara aperture."""
    if sym.shape == 'round':
        return CircleAperture(diameter=sym.size_x, unit=MM)
    elif sym.shape == 'square':
        return RectangleAperture(w=sym.size_x, h=sym.size_x, unit=MM)
    elif sym.shape == 'rect':
        return RectangleAperture(w=sym.size_x, h=sym.size_y, unit=MM)
    elif sym.shape == 'oval':
        return ObroundAperture(w=sym.size_x, h=sym.size_y, unit=MM)
    elif sym.shape == 'diamond':
        return CircleAperture(diameter=sym.size_x * 0.707, unit=MM)
    elif sym.shape == 'donut':
        return CircleAperture(diameter=sym.size_x, unit=MM)
    else:
        d = max(sym.size_x, sym.size_y)
        if d <= 0:
            d = 0.01
        return CircleAperture(diameter=d, unit=MM)


def _parse_layer_to_gerbonara(job_root, step_name, layer_name, uf, user_sym_map):
    """Parse a single ODB++ layer into a GerberFile with proper CAM objects."""
    layers_dir = os.path.join(job_root, 'steps', step_name, 'layers')
    features_path = os.path.join(layers_dir, layer_name, 'features')

    text = _read_features_text(features_path)
    if text is None:
        text = _read_features_text(features_path + '.Z')
    if text is None:
        return None, {}

    file_units = _units_from_text(text)
    layer_uf = (25.4 if file_units == 'inch' else 1.0) if file_units else uf
    sym_scale = _detect_symbol_scale(text, layer_uf)

    # InCAM Pro quirk: sym_scale ≈ 0.0254 means symbols in mils, coords in inches
    if abs(sym_scale - 0.0254) < 0.001 and layer_uf == 1.0:
        layer_uf = 25.4
        sym_scale = 0.001

    lines = text.splitlines()
    symbols = _parse_symbol_table(lines)

    if user_sym_map:
        for idx, sym in symbols.items():
            if sym.shape == 'unknown' and sym.raw_desc.lower() in user_sym_map:
                symbols[idx] = user_sym_map[sym.raw_desc.lower()]

    combined_sym_scale = layer_uf * sym_scale if layer_uf != 1.0 else sym_scale
    if combined_sym_scale != 1.0:
        for sym in symbols.values():
            sym.size_x *= combined_sym_scale
            sym.size_y *= combined_sym_scale

    # Build aperture cache
    aperture_cache = {}
    for idx, sym in symbols.items():
        if sym.shape not in ('unknown', 'skip'):
            aperture_cache[idx] = _make_aperture(sym)

    gf = GerberFile()
    stats = {'flash': 0, 'line': 0, 'region': 0, 'clear': 0, 'skip': 0}

    # Skip header lines
    feature_start = 0
    for idx, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith('$') and not s.startswith('#') \
                and not s.startswith(';') and not s.startswith('@') \
                and not s.startswith('&') \
                and not s.upper().startswith('UNITS') \
                and not s.upper().startswith('ID='):
            feature_start = idx
            break

    i = feature_start
    while i < len(lines):
        raw = lines[i].strip()
        i += 1
        if not raw or raw.startswith('#') or raw.startswith(';'):
            continue
        line_clean = raw.split(';')[0].strip()
        if not line_clean:
            continue
        parts = line_clean.split()
        rt = parts[0].upper()

        try:
            if rt in ('P', 'H'):
                x = float(parts[1]) * layer_uf
                y = float(parts[2]) * layer_uf
                sym_idx = int(parts[3])

                p4 = parts[4].split(';')[0].strip().upper() if len(parts) > 4 else ''
                if p4 in ('P', 'N'):
                    polarity = p4
                    rot = float(parts[5].split(';')[0]) if len(parts) > 5 else 0.0
                else:
                    rot = float(p4) if p4 else 0.0
                    polarity = parts[6].split(';')[0].strip().upper() if len(parts) > 6 else 'P'

                if rt == 'H':
                    polarity = 'P'

                ap = aperture_cache.get(sym_idx)
                if ap is None:
                    stats['skip'] += 1
                    continue

                sym = symbols.get(sym_idx)
                if sym and abs(rot) > 0.01 and sym.shape in ('rect', 'oval'):
                    if abs(rot % 90) < 0.1:
                        turns = int(round(rot / 90)) % 4
                        if turns in (1, 3):
                            if sym.shape == 'rect':
                                ap = RectangleAperture(w=sym.size_y, h=sym.size_x, unit=MM)
                            else:
                                ap = ObroundAperture(w=sym.size_y, h=sym.size_x, unit=MM)

                is_dark = (polarity != 'N')
                gf.objects.append(Flash(x=x, y=y, aperture=ap, unit=MM, polarity_dark=is_dark))
                stats['flash' if is_dark else 'clear'] += 1

            elif rt == 'L':
                x1 = float(parts[1]) * layer_uf
                y1 = float(parts[2]) * layer_uf
                x2 = float(parts[3]) * layer_uf
                y2 = float(parts[4]) * layer_uf
                sym_idx = int(parts[5])
                polarity = parts[6].upper() if len(parts) > 6 else 'P'

                sym = symbols.get(sym_idx)
                if sym is None:
                    stats['skip'] += 1
                    continue
                trace_ap = CircleAperture(diameter=sym.size_x, unit=MM)

                is_dark = (polarity != 'N')
                if abs(x2 - x1) < 1e-9 and abs(y2 - y1) < 1e-9:
                    gf.objects.append(Flash(x=x1, y=y1, aperture=trace_ap, unit=MM, polarity_dark=is_dark))
                else:
                    gf.objects.append(Line(x1=x1, y1=y1, x2=x2, y2=y2,
                                          aperture=trace_ap, unit=MM, polarity_dark=is_dark))
                stats['line' if is_dark else 'clear'] += 1

            elif rt == 'S':
                spol = parts[1].upper() if len(parts) > 1 else 'P'
                slines = []
                while i < len(lines):
                    sl = lines[i].strip()
                    i += 1
                    if sl.upper().startswith('SE'):
                        break
                    slines.append(sl)

                contours = []
                current = []
                for sline in slines:
                    sline = sline.split(';')[0].strip()
                    if not sline or sline.startswith('#'):
                        continue
                    sp = sline.split()
                    if not sp:
                        continue
                    cmd = sp[0].upper()
                    if cmd == 'OB':
                        current = []
                        if len(sp) >= 3:
                            current.append((float(sp[1]) * layer_uf, float(sp[2]) * layer_uf))
                    elif cmd == 'OS':
                        if len(sp) >= 3:
                            current.append((float(sp[1]) * layer_uf, float(sp[2]) * layer_uf))
                    elif cmd == 'OC':
                        if len(sp) >= 5 and current:
                            x_end = float(sp[1]) * layer_uf
                            y_end = float(sp[2]) * layer_uf
                            xc = float(sp[3]) * layer_uf
                            yc = float(sp[4]) * layer_uf
                            x_start, y_start = current[-1]
                            arc_pts = _odb_arc_to_points(x_start, y_start,
                                                          x_end, y_end, xc, yc, num_segments=32)
                            current.extend(arc_pts)
                    elif cmd == 'OE':
                        if len(current) >= 3:
                            contours.append(current)
                        current = []

                if not contours:
                    continue

                is_dark = (spol != 'N')
                exterior = contours[0]
                if exterior[0] != exterior[-1]:
                    exterior.append(exterior[0])
                gf.objects.append(Region(outline=exterior, unit=MM, polarity_dark=is_dark))

                for hole in contours[1:]:
                    if hole[0] != hole[-1]:
                        hole.append(hole[0])
                    gf.objects.append(Region(outline=hole, unit=MM, polarity_dark=not is_dark))

                stats['region'] += 1

        except Exception:
            stats['skip'] += 1
            continue

    total = stats['flash'] + stats['line'] + stats['region'] + stats['clear']
    return gf, stats


def compute_unit_positions(step_hierarchy: dict, unit_bounds: tuple,
                           panel_width: float = 510.0,
                           panel_height: float = 515.0) -> PanelLayout:
    """Walk the STEP-REPEAT hierarchy and compute absolute (x, y) for every unit.

    Recursively multiplies out NX×NY at each level from the top step (panel)
    down to the leaf step (unit).

    Args:
        step_hierarchy: Dict from _parse_step_repeat() — {step_name: [StepRepeat, ...]}.
        unit_bounds: (width_mm, height_mm) of a single unit.
        panel_width: Panel frame width in mm (always 510).
        panel_height: Panel frame height in mm (always 515).

    Returns:
        PanelLayout with all unit positions and derived grid info.
    """
    # Find the top-level step (the one not referenced as a child by anyone)
    all_children = set()
    all_parents = set()
    for parent, repeats in step_hierarchy.items():
        all_parents.add(parent)
        for sr in repeats:
            all_children.add(sr.child_step.lower())

    # Top step = parent that is not a child of anyone else
    top_steps = all_parents - all_children
    # If no clear top, try 'panel', then pick the one with most hierarchy depth
    if not top_steps:
        top_step = 'panel' if 'panel' in step_hierarchy else next(iter(step_hierarchy), None)
    elif len(top_steps) == 1:
        top_step = top_steps.pop()
    else:
        # Prefer 'panel' if available
        top_step = 'panel' if 'panel' in top_steps else sorted(top_steps)[0]

    if top_step is None:
        return PanelLayout(
            unit_positions=[(0, 0)], unit_bounds=unit_bounds,
            total_units=1, rows=1, cols=1,
            step_hierarchy=step_hierarchy,
            panel_width=panel_width, panel_height=panel_height,
        )

    def _expand(step_name: str, offset_x: float, offset_y: float) -> list:
        """Recursively expand step-repeat placements, returning leaf (unit) positions."""
        repeats = step_hierarchy.get(step_name.lower(), [])
        if not repeats:
            # Leaf step (unit) — return this position
            return [(offset_x, offset_y)]

        positions = []
        for sr in repeats:
            for iy in range(sr.ny):
                for ix in range(sr.nx):
                    child_x = offset_x + sr.x + ix * sr.dx
                    child_y = offset_y + sr.y + iy * sr.dy
                    positions.extend(_expand(sr.child_step, child_x, child_y))
        return positions

    # Start expansion from top step at origin
    positions = _expand(top_step, 0.0, 0.0)

    # Deduplicate (floating point tolerance)
    seen = set()
    unique = []
    for px, py in positions:
        key = (round(px, 3), round(py, 3))
        if key not in seen:
            seen.add(key)
            unique.append((px, py))

    # Derive rows/cols from unique Y/X values
    if unique:
        xs = sorted(set(round(p[0], 2) for p in unique))
        ys = sorted(set(round(p[1], 2) for p in unique))
        cols = len(xs)
        rows = len(ys)
    else:
        rows, cols = 1, 1

    # Save raw (pre-centering) positions for AOI coordinate normalisation.
    # AOI X_MM/Y_MM are in the same ODB++ coordinate space as these raw positions.
    raw_unique = list(unique)

    # Center positions within the panel frame (0,0)→(panel_width, panel_height)
    if unique:
        uw, uh = unit_bounds
        raw_min_x = min(p[0] for p in unique)
        raw_max_x = max(p[0] for p in unique) + uw
        raw_min_y = min(p[1] for p in unique)
        raw_max_y = max(p[1] for p in unique) + uh
        content_w = raw_max_x - raw_min_x
        content_h = raw_max_y - raw_min_y
        # Center within panel frame
        shift_x = (panel_width - content_w) / 2.0 - raw_min_x
        shift_y = (panel_height - content_h) / 2.0 - raw_min_y
        unique = [(px + shift_x, py + shift_y) for px, py in unique]

    return PanelLayout(
        unit_positions=unique,
        unit_positions_raw=raw_unique,
        unit_bounds=unit_bounds,
        total_units=len(unique),
        rows=rows,
        cols=cols,
        step_hierarchy=step_hierarchy,
        panel_width=panel_width,
        panel_height=panel_height,
    )


def render_odb_to_cam(data: bytes, filename: str = '',
                      layer_filter: list = None) -> RenderedODB:
    """
    Parse ODB++ archive and render each copper layer as CAM-quality SVG.

    Args:
        data: raw bytes of the .tgz archive
        filename: original filename (for error messages)
        layer_filter: optional list of layer names to render (None = all copper)

    Returns:
        RenderedODB with SVG strings and GerberFile objects per layer.
    """
    import shutil

    tmp_dir, job_root = _extract_odb_tgz(data)
    warnings = []

    try:
        units = _read_units(job_root)
        uf = 25.4 if units == 'inch' else 1.0

        steps_dir = os.path.join(job_root, 'steps')
        step_name = 'unit' if os.path.isdir(os.path.join(steps_dir, 'unit')) else _find_step(job_root)

        # Parse step-repeat hierarchy for panel tiling
        step_hierarchy = _parse_step_repeat(job_root, uf)

        user_sym_map = _load_user_symbols(job_root, uf)
        matrix_layers = _parse_matrix(job_root)
        if not matrix_layers:
            matrix_layers = _scan_layers_dir(os.path.join(job_root, 'steps', step_name, 'layers'))

        # Render copper + soldermask + drill layers; skip impedance test coupons (L0x_*)
        import re
        _IMPEDANCE_RE = re.compile(r'^L\d{2}_', re.IGNORECASE)
        renderable_types = {'copper', 'signal', 'power', 'mixed', 'soldermask', 'drill'}

        if layer_filter:
            selected = [(n, t) for n, t in matrix_layers if n.lower() in [l.lower() for l in layer_filter]]
        else:
            selected = [
                (n, t) for n, t in matrix_layers
                if t in renderable_types and not _IMPEDANCE_RE.match(n)
            ]

        rendered_layers = {}
        all_bounds = []

        # ── Parallel layer parsing ────────────────────────────────────────
        # Pattern for drill-span layer names like "2F-3F", "2B-3B", "1FCO-2F", etc.
        import re as _re
        _DRILL_SPAN_RE = _re.compile(
            r'^\d+[FB](CO)?[-_]\d+[FB](CO)?', _re.IGNORECASE
        )

        def _process_layer(args):
            name, ltype = args
            # Name-based drill reclassification: ODB++ sometimes exports drill span
            # layers (e.g. "2B-3B", "2F-3F") with matrix TYPE=MIXED or SIGNAL.
            # Override ltype so Region strip and colour logic apply correctly.
            if ltype != 'drill' and _DRILL_SPAN_RE.match(name):
                ltype = 'drill'
            result = _parse_layer_to_gerbonara(job_root, step_name, name, uf, user_sym_map)
            if result is None:
                return name, ltype, None, None, f"Layer '{name}': no features found"
            gf, stats = result
            if not gf.objects:
                return name, ltype, None, None, f"Layer '{name}': 0 objects parsed"
            if ltype == 'drill':
                # Drill layers may contain surface (Region) objects — board outlines
                # or copper pours — that render as a solid blob.  Strip them; only
                # flash/line features represent actual drill holes/vias.
                gf.objects = [o for o in gf.objects if not isinstance(o, Region)]
                if not gf.objects:
                    return name, ltype, None, None, f"Layer '{name}': no drill features after region strip"
                # Coordinate unit fix: ODB++ drill step features sometimes stored in
                # inches even when file declares mm → bounding box will be tiny (<5mm).
                if uf == 1.0:
                    bb = gf.bounding_box(MM)
                    extent = max(abs(bb[1][0] - bb[0][0]), abs(bb[1][1] - bb[0][1]))
                    if extent < 5.0:
                        result2 = _parse_layer_to_gerbonara(job_root, step_name, name, 25.4, user_sym_map)
                        if result2 and result2[0].objects:
                            gf, stats = result2
                            gf.objects = [o for o in gf.objects if not isinstance(o, Region)]
                # Aperture sanity: _detect_symbol_scale fails for panel-scale drill layers
                # (max_coord ≈ 500mm → mils heuristic never fires → symbols stay in mils
                # → 10mm circles instead of 0.254mm).  If any aperture looks mils-sized
                # (> 1mm and all apertures roughly proportional), apply 0.0254 correction.
                def _ap_dim(ap):
                    if hasattr(ap, 'diameter'):
                        return ap.diameter
                    return max(getattr(ap, 'w', 0), getattr(ap, 'h', 0))
                _dims = [_ap_dim(obj.aperture) for obj in gf.objects if isinstance(obj, Flash)]
                if _dims:
                    _max_ap = max(_dims)
                    # HDI laser vias are 0.05–0.15mm; PTH max ≈ 3mm.
                    # If largest aperture > 1mm, assume mils were not converted → apply 0.0254.
                    if _max_ap > 1.0:
                        _scale = 0.0254  # mils → mm
                        for obj in gf.objects:
                            if isinstance(obj, Flash):
                                _d = _ap_dim(obj.aperture)
                                obj.aperture = CircleAperture(diameter=max(_d * _scale, 0.02), unit=MM)
                        # Recompute max after mils correction
                        _dims = [_ap_dim(obj.aperture) for obj in gf.objects if isinstance(obj, Flash)]
                        _max_ap = max(_dims) if _dims else 0.0

                # Density-based overlap correction: if circles physically overlap
                # (diameter > estimated inter-via spacing), scale down to non-overlapping.
                # This catches layers where the ODB++ stores capture-pad size instead of
                # drill-hole size (e.g. 2F-3F stores 30-mil pads, not 4-mil holes).
                if _dims and len(_dims) >= 10:
                    _bb_drill = gf.bounding_box(MM)
                    _area = (abs(_bb_drill[1][0] - _bb_drill[0][0]) *
                             abs(_bb_drill[1][1] - _bb_drill[0][1]))
                    if _area > 0.01:
                        _est_spacing = (_area / len(_dims)) ** 0.5
                        if _max_ap > _est_spacing * 0.9:
                            # Circles overlap → scale to 25% of estimated spacing
                            _sf = (0.25 * _est_spacing) / _max_ap
                            for obj in gf.objects:
                                if isinstance(obj, Flash):
                                    _d = _ap_dim(obj.aperture)
                                    obj.aperture = CircleAperture(
                                        diameter=max(_d * _sf, 0.02), unit=MM
                                    )

                # panel-scale position clipping happens post-render once board_bounds is known
            return name, ltype, gf, stats, None

        parse_results = []
        with ThreadPoolExecutor(max_workers=min(4, len(selected))) as executor:
            futures = {executor.submit(_process_layer, item): item for item in selected}
            for future in as_completed(futures):
                parse_results.append(future.result())

        # ── Pre-render SVGs + data URLs (also parallelized) ───────────────
        # Filter successful parses
        valid_results = []
        for name, ltype, gf, stats, warn in parse_results:
            if warn:
                warnings.append(warn)
            elif gf is not None:
                valid_results.append((name, ltype, gf, stats))

        # Assign each layer a stacking color by index
        layer_color_map = {
            name: LAYER_COLORS[i % len(LAYER_COLORS)]
            for i, (name, _, _, _) in enumerate(valid_results)
        }

        def _render_layer(name, ltype, gf, stats):
            # Drill layers render in yellow; copper layers in copper brown
            fg_color = '#FFD700' if ltype == 'drill' else '#b87333'
            svg_str = str(gf.to_svg(fg=fg_color, bg='#060A06'))
            svg_data_url = _svg_to_data_url_fast(svg_str)

            # One stacking color (assigned by index) — not all 8
            stack_color = layer_color_map[name]
            stack_svg = str(gf.to_svg(fg=stack_color, bg='#060A06'))
            color_urls = {stack_color: _svg_to_data_url_fast(stack_svg)}

            bb = gf.bounding_box(MM)
            bounds = (bb[0][0], bb[0][1], bb[1][0], bb[1][1])
            total = stats['flash'] + stats['line'] + stats['region'] + stats['clear']

            return name, RenderedLayer(
                name=name,
                layer_type=ltype,
                svg_string=svg_str,
                svg_data_url=svg_data_url,
                color_svg_urls=color_urls,
                gerber_file=gf,
                bounds=bounds,
                feature_count=total,
                stats=stats,
            ), bounds

        # Render SVGs in parallel (2 per layer: default + stack color)
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(valid_results)))) as executor:
            render_futures = {
                executor.submit(_render_layer, name, ltype, gf, stats): name
                for name, ltype, gf, stats in valid_results
            }
            for future in as_completed(render_futures):
                name, layer_obj, bounds = future.result()
                rendered_layers[name] = layer_obj
                # Exclude drill layers from board_bounds: drill features can span
                # panel coordinates (not unit coordinates) and would inflate unit_w/unit_h.
                if layer_obj.layer_type != 'drill':
                    all_bounds.append(bounds)

        # Aggregate bounds (copper/soldermask only — determines unit dimensions)
        if all_bounds:
            board_bounds = (
                min(b[0] for b in all_bounds),
                min(b[1] for b in all_bounds),
                max(b[2] for b in all_bounds),
                max(b[3] for b in all_bounds),
            )
        else:
            board_bounds = (0, 0, 1, 1)

        # ── Clip panel-scale drill layers to unit bounds ──────────────────
        # 2B-3B and 2F-3F store all panel vias in one file (panel-level step).
        # After aperture rescaling, their Flash positions are panel-scale.
        # Filter to only vias within the unit bounding box, then shift to local coords.
        _tol = 1.0  # mm tolerance around unit bounds for edge vias
        _ux0, _uy0, _ux1, _uy1 = board_bounds
        for _dname, _dlyr in list(rendered_layers.items()):
            if _dlyr.layer_type != 'drill' or _dlyr.gerber_file is None:
                continue
            _gf = _dlyr.gerber_file
            _bb = _gf.bounding_box(MM)
            _ext = max(abs(_bb[1][0] - _bb[0][0]), abs(_bb[1][1] - _bb[0][1]))
            if _ext <= 100:
                continue  # already unit-scale, skip
            # Filter Flash/Line to unit bounds
            _kept = []
            for _obj in _gf.objects:
                if isinstance(_obj, Flash):
                    if (_ux0 - _tol <= _obj.x <= _ux1 + _tol and
                            _uy0 - _tol <= _obj.y <= _uy1 + _tol):
                        _kept.append(_obj)
                elif isinstance(_obj, Line):
                    mx = (_obj.x1 + _obj.x2) / 2
                    my = (_obj.y1 + _obj.y2) / 2
                    if (_ux0 - _tol <= mx <= _ux1 + _tol and
                            _uy0 - _tol <= my <= _uy1 + _tol):
                        _kept.append(_obj)
            if not _kept:
                del rendered_layers[_dname]
                warnings.append(f"Layer '{_dname}': no drill features within unit bounds")
                continue
            _gf.objects = _kept
            # Re-render SVG with clipped objects
            _fg = '#FFD700'
            _svg2 = str(_gf.to_svg(fg=_fg, bg='#060A06'))
            _bb2 = _gf.bounding_box(MM)
            _bounds2 = (_bb2[0][0], _bb2[0][1], _bb2[1][0], _bb2[1][1])
            _dlyr.svg_string = _svg2
            _dlyr.svg_data_url = _svg_to_data_url_fast(_svg2)
            _dlyr.bounds = _bounds2
            _dlyr.color_svg_urls = {
                next(iter(_dlyr.color_svg_urls), _fg): _svg_to_data_url_fast(
                    str(_gf.to_svg(fg=next(iter(_dlyr.color_svg_urls), _fg), bg='#060A06'))
                )
            }

        # Compute panel layout from step-repeat hierarchy + board bounds
        panel_layout = None
        if step_hierarchy:
            # ── Parse profile layer from uploaded TGZ for accurate unit dimensions ──
            # Profile defines the physical board edge (CAM standard), not copper content
            unit_w = board_bounds[2] - board_bounds[0]
            unit_h = board_bounds[3] - board_bounds[1]

            # Try to get accurate unit size from profile/board edge
            try:
                from odb_parser import _parse_features_text, _compute_bounds, _read_features_text
                import os

                profile_path = os.path.join(job_root, 'steps', step_name, 'profile')
                profile_text = _read_features_text(profile_path)
                if profile_text is None:
                    profile_text = _read_features_text(profile_path + '.Z')

                if profile_text:
                    unknown_symbols_dummy = set()
                    geoms, widths, warns, _, _ = _parse_features_text(profile_text, uf, unknown_symbols_dummy)
                    if geoms:
                        pb = _compute_bounds(geoms)
                        if pb:
                            # Profile bounds: (min_x, min_y, max_x, max_y)
                            profile_w = pb[2] - pb[0]
                            profile_h = pb[3] - pb[1]
                            # Use profile dimensions if they're reasonable (within ±25% of copper bounds)
                            # Profile is CAM standard for unit sizing; override copper which includes rails
                            if profile_w > 0 and profile_h > 0:
                                copper_tolerance = 0.25  # ±25% tolerance (profile vs copper with rails)
                                if (abs(profile_w - unit_w) / unit_w <= copper_tolerance and
                                    abs(profile_h - unit_h) / unit_h <= copper_tolerance):
                                    unit_w = profile_w
                                    unit_h = profile_h
                                    warnings.append(f"✅ Unit size from board profile: {unit_w:.2f}×{unit_h:.2f} mm (CAM standard)")
                                else:
                                    warnings.append(
                                        f"⚠️ Profile ({profile_w:.2f}×{profile_h:.2f} mm) vs copper ({unit_w:.2f}×{unit_h:.2f} mm) "
                                        f"differ >25% — using copper"
                                    )
            except Exception as e:
                # Gracefully fall back to copper bounds if profile parsing fails
                warnings.append(f"⚠️ Could not parse profile layer ({e}) — using copper bounds")

            # Detect InCAM Pro inches quirk: if the smallest DX/DY in the hierarchy
            # is much smaller than the unit width, coordinates are likely in inches
            _all_spacings = []
            for _sr_list in step_hierarchy.values():
                for _sr in _sr_list:
                    if _sr.dx > 0:
                        _all_spacings.append(_sr.dx)
                    if _sr.dy > 0:
                        _all_spacings.append(_sr.dy)
            if _all_spacings and unit_w > 10:
                _min_spacing = min(_all_spacings)
                if _min_spacing < 5.0 and _min_spacing * 25.4 > unit_w * 0.8:
                    step_hierarchy = _parse_step_repeat(job_root, 25.4)

            panel_layout = compute_unit_positions(
                step_hierarchy, (unit_w, unit_h),
            )

        # Build panel SVG for the first copper layer only (used as Panel Overview background)
        if panel_layout and rendered_layers:
            _first_copper = next(
                (lo for lo in rendered_layers.values() if lo.layer_type != 'drill'),
                None
            )
            if _first_copper:
                try:
                    _first_copper.panel_svg_data_url = build_panel_svg(
                        _first_copper.svg_string, panel_layout
                    )
                except Exception:
                    pass

        return RenderedODB(
            layers=rendered_layers,
            board_bounds=board_bounds,
            step_name=step_name,
            units=units,
            panel_layout=panel_layout,
            warnings=warnings,
        )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def scan_available_layers(data: bytes) -> list[tuple[str, str]]:
    """
    Quick scan of ODB++ archive — returns [(name, type), ...] for renderable layers.
    No geometry parsing, just reads the matrix file. Takes ~0.1s.
    """
    import re
    import shutil
    _IMPEDANCE_RE = re.compile(r'^L\d{2}_', re.IGNORECASE)
    renderable_types = {'copper', 'signal', 'power', 'mixed', 'soldermask', 'drill'}

    tmp_dir, job_root = _extract_odb_tgz(data)
    try:
        matrix_layers = _parse_matrix(job_root)
        if not matrix_layers:
            steps_dir = os.path.join(job_root, 'steps')
            step_name = 'unit' if os.path.isdir(os.path.join(steps_dir, 'unit')) else _find_step(job_root)
            matrix_layers = _scan_layers_dir(os.path.join(job_root, 'steps', step_name, 'layers'))
        return [
            (n, t) for n, t in matrix_layers
            if t in renderable_types and not _IMPEDANCE_RE.match(n)
        ]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def render_layer_svg(data: bytes, layer_name: str,
                     fg_color: str = '#b87333', bg_color: str = '#060A06') -> Optional[str]:
    """Convenience: render a single layer and return SVG string."""
    result = render_odb_to_cam(data, layer_filter=[layer_name])
    layer = result.layers.get(layer_name) or next(iter(result.layers.values()), None)
    if layer:
        # Re-render with custom colors
        svg_tag = layer.gerber_file.to_svg(fg=fg_color, bg=bg_color)
        return str(svg_tag)
    return None
