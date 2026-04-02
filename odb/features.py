"""odb/features.py — Features file I/O and full feature parser."""

from typing import Optional

from shapely.ops import unary_union

from odb.constants import MAX_FEATURE_ERRORS, INCHES_TO_MM
from odb.symbols import parse_symbol_table
from odb.geometry import parse_pad_record, parse_line_record, parse_arc_record, parse_surface_block


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def read_features_text(path: str) -> Optional[str]:
    """
    Read a features file, transparently handling Unix .compress (.Z) format
    and the plain .Z fallback variant.

    ODB++ features are usually plain ASCII. Some exporters write them with
    Unix compress (LZW) — detectable by magic bytes 0x1F 0x9D.

    Returns the decoded text, or None if the file cannot be read.
    """
    # Try plain path first, then .Z variant
    for candidate in (path, path + '.Z') if not path.endswith('.Z') else (path,):
        try:
            raw = open(candidate, 'rb').read()
        except (OSError, IOError):
            continue

        if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x9D:
            try:
                from unlzw3 import unlzw
                raw = unlzw(raw)
            except (ImportError, Exception):
                continue

        return raw.decode('latin-1', errors='replace')

    return None


# ---------------------------------------------------------------------------
# Symbol resolution helpers (internal)
# ---------------------------------------------------------------------------

def _resolve_symbols(lines: list, uf: float, user_sym_map: dict,
                     unknown_symbols: set, sym_scale: float) -> dict:
    """Build and scale the aperture dict from $ lines."""
    symbols = parse_symbol_table(lines)

    if user_sym_map:
        for idx, sym in symbols.items():
            if sym.shape == 'unknown' and sym.raw_desc.lower() in user_sym_map:
                symbols[idx] = user_sym_map[sym.raw_desc.lower()]

    for sym in symbols.values():
        if sym.shape in ('unknown', 'skip'):
            unknown_symbols.add(sym.raw_desc)

    combined_scale = uf * sym_scale if uf != 1.0 else sym_scale
    if combined_scale != 1.0:
        for sym in symbols.values():
            sym.size_x *= combined_scale
            sym.size_y *= combined_scale

    return symbols


def _find_feature_start(lines: list) -> int:
    """Return index of first feature line (skip $, #, ;, UNITS=, ID= headers)."""
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith('$') \
               and not s.startswith('#') \
               and not s.startswith(';') \
               and not s.upper().startswith('UNITS') \
               and not s.upper().startswith('ID='):
            return i
    return 0


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_features_text(text: str, uf: float, unknown_symbols: set,
                         user_symbols: Optional[dict] = None,
                         sym_scale: float = 1.0) -> tuple:
    """
    Parse a full ODB++ features file text into Shapely geometries.

    Returns (geometries, trace_widths, warnings, fiducials, drill_hits).
    drill_hits is a list of (x, y, diameter_mm) tuples from H records.
    """
    pos_geoms = []
    neg_geoms = []
    trace_widths = []
    warnings = []
    fiducials = []
    drill_hits = []

    lines = text.splitlines()
    symbols = _resolve_symbols(lines, uf, user_symbols or {}, unknown_symbols, sym_scale)
    feature_start = _find_feature_start(lines)

    i = feature_start
    error_count = 0

    while i < len(lines):
        raw_line = lines[i].strip()
        i += 1

        if not raw_line or raw_line.startswith('#') or raw_line.startswith(';'):
            continue

        is_fiducial = '.fiducial' in raw_line.lower()
        line = raw_line.split(';')[0].strip()
        if not line:
            continue

        parts = line.split()
        record_type = parts[0].upper()

        try:
            if record_type in ('P', 'H'):
                if is_fiducial and len(parts) >= 3:
                    try:
                        fiducials.append((float(parts[1]) * uf, float(parts[2]) * uf))
                    except ValueError:
                        pass

                if record_type == 'H' and len(parts) >= 4:
                    try:
                        hx = float(parts[1]) * uf
                        hy = float(parts[2]) * uf
                        sym = symbols.get(int(parts[3]))
                        diam = max(sym.size_x, sym.size_y) if sym else 0.1
                        drill_hits.append((hx, hy, diam))
                    except (ValueError, IndexError):
                        pass

                geom, polarity = parse_pad_record(
                    parts, symbols, uf,
                    force_positive=(record_type == 'H'),
                    ignore_polarity=True,
                )
                if geom is not None:
                    sym_idx = int(parts[3]) if len(parts) > 3 else -1
                    sym = symbols.get(sym_idx)
                    width = max(sym.size_x, sym.size_y) if sym else 0.0
                    if polarity == 'N' and record_type != 'H':
                        neg_geoms.append(geom)
                    else:
                        pos_geoms.append(geom)
                        trace_widths.append(width)

            elif record_type == 'L':
                geom, polarity = parse_line_record(parts, symbols, uf, ignore_polarity=True)
                if geom is not None:
                    sym_idx = int(parts[5]) if len(parts) > 5 else -1
                    sym = symbols.get(sym_idx)
                    width = sym.size_x if sym else 0.0
                    if polarity == 'N':
                        neg_geoms.append(geom)
                    else:
                        pos_geoms.append(geom)
                        trace_widths.append(width)

            elif record_type == 'S':
                surf_polarity = parts[1].upper() if len(parts) > 1 else 'P'
                surface_lines = []
                while i < len(lines):
                    sline = lines[i].strip()
                    i += 1
                    if sline.upper().startswith('SE'):
                        break
                    surface_lines.append(sline)
                geom = parse_surface_block(surface_lines, uf)
                if geom is not None:
                    if surf_polarity == 'N':
                        neg_geoms.append(geom)
                    else:
                        pos_geoms.append(geom)
                        minx, miny, maxx, maxy = geom.bounds
                        trace_widths.append(max(maxx - minx, maxy - miny))

            elif record_type == 'A':
                geom, polarity = parse_arc_record(parts, symbols, uf, ignore_polarity=True)
                if geom is not None:
                    sym_idx = int(parts[7]) if len(parts) > 7 else -1
                    sym = symbols.get(sym_idx)
                    width = sym.size_x if sym else 0.0
                    if polarity == 'N':
                        neg_geoms.append(geom)
                    else:
                        pos_geoms.append(geom)
                        trace_widths.append(width)

            # T (text), B (barcode) — not physical copper; not rendered

        except Exception as e:
            error_count += 1
            if error_count <= MAX_FEATURE_ERRORS:
                warnings.append(f"Feature parse error at line {i}: {e}")

    # Apply negative-polarity subtraction (clearances cut into copper planes)
    if neg_geoms and pos_geoms:
        try:
            result = unary_union(pos_geoms).difference(unary_union(neg_geoms))
            if result.geom_type == 'MultiPolygon':
                geometries = list(result.geoms)
            elif result.geom_type in ('Polygon', 'GeometryCollection'):
                geometries = [g for g in (result.geoms if hasattr(result, 'geoms') else [result])
                              if not g.is_empty]
            else:
                geometries = [result] if not result.is_empty else pos_geoms
            trace_widths = [0.0] * len(geometries)
        except Exception as e:
            warnings.append(f"Negative polarity subtraction failed: {e} — using positive geoms only")
            geometries = pos_geoms
    else:
        geometries = pos_geoms

    return geometries, trace_widths, warnings, fiducials, drill_hits


# ---------------------------------------------------------------------------
# Bounds helpers
# ---------------------------------------------------------------------------

def compute_bounds(geoms: list) -> tuple:
    """Compute (xmin, ymin, xmax, ymax) from a list of Shapely geometries."""
    if not geoms:
        return (0.0, 0.0, 0.0, 0.0)
    try:
        b = unary_union(geoms).bounds
        return (b[0], b[1], b[2], b[3])
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)


def aggregate_bounds(layers: dict) -> tuple:
    """Aggregate board bounds across all ODBLayer objects."""
    valid = [l.bounds for l in layers.values()
             if l.bounds and l.bounds != (0.0, 0.0, 0.0, 0.0)]
    if not valid:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(b[0] for b in valid),
        min(b[1] for b in valid),
        max(b[2] for b in valid),
        max(b[3] for b in valid),
    )
