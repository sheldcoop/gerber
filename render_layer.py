#!/usr/bin/env python3
"""
render_layer.py — Render ODB++ copper layer via Gerbonara (CAM-quality).

Parses ODB++ features → maps to Gerbonara Flash/Line/Region → renders SVG/Gerber.
Each pad, trace, and surface keeps its identity — renders like InCAM Pro.

Usage:
    python3 render_layer.py fhr0010_bkm.tgz 3f
    python3 render_layer.py fhr0010_bkm.tgz 3f 2f
    python3 render_layer.py fhr0010_bkm.tgz           # all copper
"""

import math
import os
import sys
import time

from gerbonara import GerberFile
from gerbonara.graphic_objects import Flash, Line, Arc, Region
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
        # Approximate as circle with equivalent area
        return CircleAperture(diameter=sym.size_x * 0.707, unit=MM)
    elif sym.shape == 'donut':
        # Outer circle only (hole handled separately)
        return CircleAperture(diameter=sym.size_x, unit=MM)
    else:
        # Fallback: circle with max dimension
        d = max(sym.size_x, sym.size_y)
        if d <= 0:
            d = 0.01
        return CircleAperture(diameter=d, unit=MM)


def parse_to_gerbonara(job_root, step_name, layer_name, uf, user_sym_map):
    """
    Parse ODB++ features and build a GerberFile with proper objects.
    Returns GerberFile with Flash/Line/Region objects.
    """
    layers_dir = os.path.join(job_root, 'steps', step_name, 'layers')
    features_path = os.path.join(layers_dir, layer_name, 'features')

    text = _read_features_text(features_path)
    if text is None:
        text = _read_features_text(features_path + '.Z')
    if text is None:
        print(f"  WARNING: no features file for '{layer_name}'")
        return None

    file_units = _units_from_text(text)
    layer_uf = (25.4 if file_units == 'inch' else 1.0) if file_units else uf
    sym_scale = _detect_symbol_scale(text, layer_uf)

    # InCAM Pro quirk: if sym_scale ≈ 0.0254, symbols are in mils → coords are in inches
    # Override uf to convert coordinates to mm
    if abs(sym_scale - 0.0254) < 0.001 and layer_uf == 1.0:
        layer_uf = 25.4  # coordinates are inches, convert to mm
        sym_scale = 0.001  # mils → mm directly (not via uf since uf now handles coords)

    lines = text.splitlines()
    symbols = _parse_symbol_table(lines)

    # Resolve user-defined symbols
    if user_sym_map:
        for idx, sym in symbols.items():
            if sym.shape == 'unknown' and sym.raw_desc.lower() in user_sym_map:
                symbols[idx] = user_sym_map[sym.raw_desc.lower()]

    # Scale symbol sizes to mm
    combined_sym_scale = layer_uf * sym_scale if layer_uf != 1.0 else sym_scale
    if combined_sym_scale != 1.0:
        for sym in symbols.values():
            sym.size_x *= combined_sym_scale
            sym.size_y *= combined_sym_scale

    # Build aperture cache (one Gerbonara aperture per ODB++ symbol index)
    aperture_cache = {}
    for idx, sym in symbols.items():
        if sym.shape not in ('unknown', 'skip'):
            aperture_cache[idx] = _make_aperture(sym)

    gf = GerberFile()
    stats = {'flash': 0, 'line': 0, 'arc': 0, 'region': 0, 'clear': 0, 'skip': 0}

    # Skip header
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
            # ── PAD (Flash) ──────────────────────────────────────────────
            if rt in ('P', 'H'):
                x = float(parts[1]) * layer_uf
                y = float(parts[2]) * layer_uf
                sym_idx = int(parts[3])

                # Detect polarity field position (Format A vs Format B)
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

                # For rotated rectangular pads, create a rotated aperture
                sym = symbols.get(sym_idx)
                if sym and abs(rot) > 0.01 and sym.shape in ('rect', 'oval', 'square'):
                    # Gerbonara Flash doesn't support rotation directly for all apertures.
                    # For 90° rotations of rectangles, swap w/h
                    if sym.shape in ('rect', 'oval') and abs(rot % 90) < 0.1:
                        turns = int(round(rot / 90)) % 4
                        if turns in (1, 3):
                            if sym.shape == 'rect':
                                ap = RectangleAperture(w=sym.size_y, h=sym.size_x, unit=MM)
                            else:
                                ap = ObroundAperture(w=sym.size_y, h=sym.size_x, unit=MM)

                is_dark = (polarity != 'N')
                flash = Flash(x=x, y=y, aperture=ap, unit=MM, polarity_dark=is_dark)
                gf.objects.append(flash)

                if is_dark:
                    stats['flash'] += 1
                else:
                    stats['clear'] += 1

            # ── TRACE (Line) ─────────────────────────────────────────────
            elif rt == 'L':
                x1 = float(parts[1]) * layer_uf
                y1 = float(parts[2]) * layer_uf
                x2 = float(parts[3]) * layer_uf
                y2 = float(parts[4]) * layer_uf
                sym_idx = int(parts[5])
                polarity = parts[6].upper() if len(parts) > 6 else 'P'

                ap = aperture_cache.get(sym_idx)
                if ap is None:
                    stats['skip'] += 1
                    continue

                # For traces, always use CircleAperture (round cap)
                sym = symbols.get(sym_idx)
                if sym:
                    trace_ap = CircleAperture(diameter=sym.size_x, unit=MM)
                else:
                    trace_ap = ap

                is_dark = (polarity != 'N')

                # Zero-length line = flash
                if abs(x2 - x1) < 1e-9 and abs(y2 - y1) < 1e-9:
                    flash = Flash(x=x1, y=y1, aperture=trace_ap, unit=MM, polarity_dark=is_dark)
                    gf.objects.append(flash)
                else:
                    ln = Line(x1=x1, y1=y1, x2=x2, y2=y2, aperture=trace_ap,
                              unit=MM, polarity_dark=is_dark)
                    gf.objects.append(ln)

                if is_dark:
                    stats['line'] += 1
                else:
                    stats['clear'] += 1

            # ── SURFACE (Region) ─────────────────────────────────────────
            elif rt == 'S':
                spol = parts[1].upper() if len(parts) > 1 else 'P'
                slines = []
                while i < len(lines):
                    sl = lines[i].strip()
                    i += 1
                    if sl.upper().startswith('SE'):
                        break
                    slines.append(sl)

                # Parse surface contours
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
                            cx, cy = float(sp[1]) * layer_uf, float(sp[2]) * layer_uf
                            current.append((cx, cy))
                    elif cmd == 'OS':
                        if len(sp) >= 3:
                            cx, cy = float(sp[1]) * layer_uf, float(sp[2]) * layer_uf
                            current.append((cx, cy))
                    elif cmd == 'OC':
                        if len(sp) >= 5 and current:
                            x_end = float(sp[1]) * layer_uf
                            y_end = float(sp[2]) * layer_uf
                            xc = float(sp[3]) * layer_uf
                            yc = float(sp[4]) * layer_uf
                            x_start, y_start = current[-1]
                            arc_pts = _odb_arc_to_points(x_start, y_start,
                                                          x_end, y_end, xc, yc,
                                                          num_segments=32)
                            current.extend(arc_pts)
                    elif cmd == 'OE':
                        if len(current) >= 3:
                            contours.append(current)
                        current = []

                if not contours:
                    continue

                is_dark = (spol != 'N')

                # First contour = exterior, rest = holes
                # Gerbonara Region takes a flat outline (no holes natively)
                # For each contour, create a separate Region
                exterior = contours[0]
                # Close the contour
                if exterior[0] != exterior[-1]:
                    exterior.append(exterior[0])

                region = Region(outline=exterior, unit=MM, polarity_dark=is_dark)
                gf.objects.append(region)

                # Holes as clear regions
                for hole in contours[1:]:
                    if hole[0] != hole[-1]:
                        hole.append(hole[0])
                    hole_region = Region(outline=hole, unit=MM, polarity_dark=not is_dark)
                    gf.objects.append(hole_region)

                stats['region'] += 1

        except Exception as e:
            stats['skip'] += 1
            continue

    return gf, stats


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    tgz_path = sys.argv[1]
    requested = [n.lower() for n in sys.argv[2:]]

    print(f"Parsing {tgz_path} ...")
    t0 = time.time()

    with open(tgz_path, 'rb') as f:
        data = f.read()
    tmp_dir, job_root = _extract_odb_tgz(data)

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
        if requested:
            selected = [(n, t) for n, t in matrix_layers if n.lower() in requested]
        else:
            selected = [(n, t) for n, t in matrix_layers if t in copper_types]

        if not selected:
            print(f"No matching layers. Available: {[n for n, _ in matrix_layers]}")
            sys.exit(1)

        print(f"  Units: {units} (uf={uf}), Step: {step_name}")
        print(f"  Layers: {[n for n, _ in selected]}")

        base = os.path.splitext(os.path.basename(tgz_path))[0]

        for name, ltype in selected:
            print(f"\n  === Layer: {name} ===")
            t1 = time.time()

            result = parse_to_gerbonara(job_root, step_name, name, uf, user_sym_map)
            if result is None:
                continue
            gf, stats = result

            t_parse = time.time() - t1
            print(f"  Parsed in {t_parse:.1f}s")
            print(f"  Stats: {stats}")
            print(f"  Total Gerbonara objects: {len(gf.objects)}")

            # Output file names
            layer_base = f"{base}_{name}"

            # Save as Gerber (RS-274X)
            gbr_path = f"{layer_base}.gbr"
            gf.save(gbr_path)
            print(f"  Saved Gerber: {gbr_path}")

            # Save as SVG (CAM-quality vector)
            svg_path = f"{layer_base}.svg"
            svg = gf.to_svg(fg='#b8733380', bg='#060A06')
            with open(svg_path, 'w') as f:
                f.write(str(svg))
            print(f"  Saved SVG: {svg_path}")

        total = time.time() - t0
        print(f"\n  Total time: {total:.1f}s")
        print(f"  Open .svg in browser for CAM view, .gbr in any Gerber viewer.")

    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == '__main__':
    main()
