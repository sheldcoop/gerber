"""
gerber_renderer.py — ODB++ to Gerbonara CAM-quality SVG renderer.

Parses ODB++ features → Gerbonara Flash/Line/Region objects → SVG.
Each pad, trace, and surface retains its identity for CAM-quality rendering.
"""

import math
import os
from dataclasses import dataclass, field
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
)


@dataclass
class RenderedLayer:
    """A single rendered copper layer."""
    name: str
    layer_type: str
    svg_string: str
    gerber_file: GerberFile
    bounds: tuple  # (min_x, min_y, max_x, max_y) in mm
    feature_count: int
    stats: dict = field(default_factory=dict)


@dataclass
class RenderedODB:
    """All rendered layers from an ODB++ archive."""
    layers: dict  # name → RenderedLayer
    board_bounds: tuple  # aggregate (min_x, min_y, max_x, max_y) in mm
    step_name: str = ''
    units: str = ''
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

        user_sym_map = _load_user_symbols(job_root, uf)
        matrix_layers = _parse_matrix(job_root)
        if not matrix_layers:
            matrix_layers = _scan_layers_dir(os.path.join(job_root, 'steps', step_name, 'layers'))

        copper_types = {'copper', 'signal', 'power', 'mixed'}

        if layer_filter:
            selected = [(n, t) for n, t in matrix_layers if n.lower() in [l.lower() for l in layer_filter]]
        else:
            selected = [(n, t) for n, t in matrix_layers if t in copper_types]

        rendered_layers = {}
        all_bounds = []

        for name, ltype in selected:
            result = _parse_layer_to_gerbonara(job_root, step_name, name, uf, user_sym_map)
            if result is None:
                warnings.append(f"Layer '{name}': no features found")
                continue

            gf, stats = result
            if not gf.objects:
                warnings.append(f"Layer '{name}': 0 objects parsed")
                continue

            # Render SVG
            svg_tag = gf.to_svg(fg='#b87333', bg='#060A06')
            svg_str = str(svg_tag)

            # Get bounds
            bb = gf.bounding_box(MM)
            bounds = (bb[0][0], bb[0][1], bb[1][0], bb[1][1])
            all_bounds.append(bounds)

            total = stats['flash'] + stats['line'] + stats['region'] + stats['clear']
            rendered_layers[name] = RenderedLayer(
                name=name,
                layer_type=ltype,
                svg_string=svg_str,
                gerber_file=gf,
                bounds=bounds,
                feature_count=total,
                stats=stats,
            )

        # Aggregate bounds
        if all_bounds:
            board_bounds = (
                min(b[0] for b in all_bounds),
                min(b[1] for b in all_bounds),
                max(b[2] for b in all_bounds),
                max(b[3] for b in all_bounds),
            )
        else:
            board_bounds = (0, 0, 1, 1)

        return RenderedODB(
            layers=rendered_layers,
            board_bounds=board_bounds,
            step_name=step_name,
            units=units,
            warnings=warnings,
        )

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
