"""
odb_parser.py — ODB++ archive parser for PCB layer geometry.

Extracts layer polygons from an ODB++ .tgz archive and returns them as
Shapely geometries for use with the Plotly visualizer.

ODB++ features file format (plain text, one record per line):
  $ lines       — symbol/aperture table (indexed by integer)
  P x y sym rotation mirror polarity [;attrs]  — pad/flash
  L x1 y1 x2 y2 sym polarity [;attrs]          — line/trace
  S polarity [;attrs]                           — surface begin
    OB x y I                                   — contour begin
    OS x y                                     — line segment
    OC x y xc yc                               — arc segment
    OE                                         — contour end
  SE                                           — surface end

Coordinate units: mm by default; inches converted via ×25.4.
"""

import io
import math
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from shapely.geometry import Point, Polygon, LineString
from shapely.geometry import box as shapely_box
from shapely.affinity import rotate as shapely_rotate
from shapely.ops import unary_union


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class _ODBSymbol:
    """Aperture/symbol definition from the $ table in a features file."""
    shape: str    # 'round', 'square', 'rect', 'oval', 'diamond', 'unknown', 'skip'
    size_x: float # primary size: diameter (round), side (square), width (rect/oval)
    size_y: float # secondary size: height for rect/oval; equals size_x otherwise
    raw_desc: str = '' # original descriptor string


@dataclass
class ODBLayer:
    """
    Single parsed ODB++ layer — mirrors GerberLayer interface so visualizer.py
    works unchanged.
    """
    name: str
    layer_type: str     # 'copper', 'soldermask', 'silkscreen', 'paste',
                        # 'drill', 'outline', 'other'
    polygons: list      # list of Shapely geometry objects
    bounds: tuple       # (xmin, ymin, xmax, ymax) in mm
    polygon_count: int = 0
    trace_widths: list = field(default_factory=list)  # parallel list: feature width in mm per polygon
    warnings: list = field(default_factory=list)


@dataclass
class DrillHit:
    """Single drill hit extracted from a drill layer."""
    x: float
    y: float
    diameter: float  # mm
    layer_name: str = ''


@dataclass
class ComponentPlacement:
    """Single component placement record from ODB++ components file."""
    refdes: str
    part_type: str
    x: float          # center X in mm
    y: float          # center Y in mm
    rotation: float   # degrees
    mirror: bool      # True = bottom side
    side: str         # 'T' or 'B'


@dataclass
class ParsedODB:
    """
    Result of parsing an ODB++ archive.
    """
    layers: dict        # {layer_name: ODBLayer}
    board_bounds: tuple # (xmin, ymin, xmax, ymax) in mm
    step_name: str
    units: str          # 'mm' or 'inch'
    origin_x: float = 0.0
    origin_y: float = 0.0
    unknown_symbols: set = field(default_factory=set)
    fiducials: list = field(default_factory=list)
    drill_hits: list = field(default_factory=list)        # list[DrillHit]
    components: list = field(default_factory=list)        # list[ComponentPlacement]
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# ODB++ matrix TYPE → internal layer_type mapping
# ---------------------------------------------------------------------------

_MATRIX_TYPE_MAP = {
    'SIGNAL':       'copper',
    'POWER':        'copper',
    'MIXED':        'copper',
    'POWER_GROUND': 'copper',
    'SOLDER_MASK':  'soldermask',
    'MASK':         'soldermask',
    'SILK_SCREEN':  'silkscreen',
    'SOLDER_PASTE': 'paste',
    'DRILL':        'drill',
    'ROUT':         'drill',
    'PROFILE':      'outline',
    'DIELECTRIC':   'other',
    'DOCUMENT':     'other',
    'COMPONENT':    'other',
}


# ---------------------------------------------------------------------------
# Archive extraction
# ---------------------------------------------------------------------------

def _extract_odb_tgz(data: bytes) -> tuple:
    """
    Extract an ODB++ .tgz archive to a temporary directory.

    Returns (tmp_dir, job_root_path).
    job_root is the top-level directory inside the archive (the job name).

    Security: members with absolute paths or '..' are filtered out to prevent
    path traversal attacks from untrusted archives.
    """
    tmp = tempfile.mkdtemp(prefix='odb_')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tf:
        safe_members = [
            m for m in tf.getmembers()
            if not os.path.isabs(m.name) and '..' not in m.name.split('/')
        ]
        tf.extractall(tmp, members=safe_members)

    # The job root is the single top-level directory inside the archive
    entries = [e for e in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, e))]
    job_name = entries[0] if entries else ''
    job_root = os.path.join(tmp, job_name) if job_name else tmp
    return tmp, job_root


# ---------------------------------------------------------------------------
# Units detection
# ---------------------------------------------------------------------------

def _read_units(job_root: str) -> str:
    """
    Detect coordinate units from misc/info (contains UNITS=INCH or UNITS=MM).

    Falls back to 'mm' if the file is missing or unreadable.
    The features file header also contains a UNITS= line and is checked as
    a secondary source (caller passes that text optionally).
    """
    info_path = os.path.join(job_root, 'misc', 'info')
    try:
        with open(info_path, 'r', errors='ignore') as f:
            for line in f:
                line = line.strip().upper()
                if line.startswith('UNITS'):
                    if 'INCH' in line:
                        return 'inch'
                    if 'MM' in line or 'METRIC' in line:
                        return 'mm'
    except (OSError, IOError):
        pass
    return 'mm'


def _units_from_text(text: str) -> Optional[str]:
    """
    Extract units from the header section of a features file text.
    Returns 'mm', 'inch', or None if not found.
    """
    for line in text.splitlines()[:30]:  # check only header lines
        upper = line.strip().upper()
        if upper.startswith('UNITS'):
            if 'INCH' in upper:
                return 'inch'
            if 'MM' in upper or 'METRIC' in upper:
                return 'mm'
    return None


def _read_design_origin(job_root: str, uf: float) -> tuple[float, float]:
    """Read design origin from misc/attrlist."""
    attrlist_path = os.path.join(job_root, 'misc', 'attrlist')
    ox, oy = 0.0, 0.0
    try:
        with open(attrlist_path, 'r', errors='ignore') as f:
            for line in f:
                if line.startswith('.design_origin_x='):
                    ox = float(line.strip().split('=')[1]) * uf
                elif line.startswith('.design_origin_y='):
                    oy = float(line.strip().split('=')[1]) * uf
    except (OSError, IOError, ValueError, IndexError):
        pass
    return ox, oy


# ---------------------------------------------------------------------------
# Matrix parsing (block format)
# ---------------------------------------------------------------------------

def _parse_matrix(job_root: str) -> list:
    """
    Parse matrix/matrix to get the ordered layer list.

    ODB++ matrix format is block-based:
        LAYER {
            ROW=4
            TYPE=SIGNAL
            NAME=SIGNAL_1
            POLARITY=POSITIVE
            CONTEXT=BOARD
        }

    Returns list of (layer_name, layer_type_string) in file order.
    """
    matrix_path = os.path.join(job_root, 'matrix', 'matrix')
    layers = []

    try:
        with open(matrix_path, 'r', errors='ignore') as f:
            content = f.read()
    except (OSError, IOError):
        return layers

    # Extract all LAYER {...} blocks
    block_pattern = re.compile(r'LAYER\s*\{([^}]+)\}', re.IGNORECASE | re.DOTALL)
    kv_pattern = re.compile(r'^\s*([A-Z_]+)\s*=\s*(.+?)\s*$', re.IGNORECASE | re.MULTILINE)

    for block_match in block_pattern.finditer(content):
        block_text = block_match.group(1)
        props = {m.group(1).upper(): m.group(2).strip() for m in kv_pattern.finditer(block_text)}

        name = props.get('NAME', '').strip()
        raw_type = props.get('TYPE', '').upper()

        if not name:
            continue

        layer_type = _MATRIX_TYPE_MAP.get(raw_type, 'other')
        layers.append((name, layer_type))

    return layers


def _scan_layers_dir(layers_dir: str) -> list:
    """
    Fallback when matrix/matrix is missing: scan the layers directory and
    classify by name heuristics.
    """
    if not os.path.isdir(layers_dir):
        return []

    name_patterns = [
        (re.compile(r'signal|copper|top|bot|inner|l\d', re.I), 'copper'),
        (re.compile(r'solder.?mask|mask_top|mask_bot', re.I),   'soldermask'),
        (re.compile(r'silk|legend|overlay', re.I),               'silkscreen'),
        (re.compile(r'paste|cream', re.I),                       'paste'),
        (re.compile(r'drill|via|hole', re.I),                    'drill'),
        (re.compile(r'outline|profile|board.?edge|contour', re.I), 'outline'),
    ]

    result = []
    for entry in sorted(os.listdir(layers_dir)):
        if not os.path.isdir(os.path.join(layers_dir, entry)):
            continue
        layer_type = 'other'
        for pattern, ltype in name_patterns:
            if pattern.search(entry):
                layer_type = ltype
                break
        result.append((entry, layer_type))
    return result


# ---------------------------------------------------------------------------
# Step directory discovery
# ---------------------------------------------------------------------------

def _find_step(job_root: str) -> str:
    """
    Find the primary step directory inside steps/.
    Always uses the first directory found alphabetically.
    """
    steps_dir = os.path.join(job_root, 'steps')
    try:
        entries = sorted([
            e for e in os.listdir(steps_dir)
            if os.path.isdir(os.path.join(steps_dir, e))
        ])
    except OSError:
        return 'pcb'

    if not entries:
        return 'pcb'

    return entries[0]


# ---------------------------------------------------------------------------
# Features file I/O (handles plain text and .Z compression)
# ---------------------------------------------------------------------------

def _read_features_text(path: str) -> Optional[str]:
    """
    Read a features file, transparently handling Unix .compress (.Z) format.

    ODB++ features are usually plain ASCII. Some exporters write them with
    Unix compress (LZW) — detectable by magic bytes 0x1F 0x9D.

    Returns the decoded text, or None if the file cannot be read.
    """
    try:
        raw = open(path, 'rb').read()
    except (OSError, IOError):
        return None

    # Check for Unix compress magic: 0x1F 0x9D
    if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x9D:
        try:
            from unlzw3 import unlzw
            raw = unlzw(raw)
        except ImportError:
            # unlzw3 not installed — can't decompress, skip this file
            return None
        except Exception:
            return None

    # Use latin-1: safe for all byte values 0-255, ODB++ is ASCII + possible
    # extended chars in attribute strings
    return raw.decode('latin-1', errors='replace')


# ---------------------------------------------------------------------------
# Symbol table parser
# ---------------------------------------------------------------------------

_SHAPE_PREFIX_MAP = {
    'r':         'round',  'round':     'round',
    's':         'square', 'sq':        'square', 'square': 'square',
    'rect':      'rect',   'rectangle': 'rect',
    'oval':      'oval',   'oblong':    'oval',   'obround': 'oval',
    'di':        'diamond','diamond':   'diamond',
    'donut':     'donut',  'donut_r':   'donut',  'donut_s': 'donut',
    'hex_l':     'other',  'hex_s':     'other',
    'oct_l':     'other',  'oct_s':     'other',
    'ellipse':   'oval',
    'moire':     'other',  'thermal':   'other',
    'tri':       'other',  'bfr125':    'other',
    'ifr125':    'other',  'sr125':     'other',
    'rc':        'rect',   'brc':       'rect',
}


def _parse_symbol_descriptor(desc: str) -> _ODBSymbol:
    """
    Parse a single symbol descriptor.
    Handles known types and safely falls back for unknowns.
    """
    desc = desc.strip().lower()

    if desc.startswith('special_') or desc.startswith('sc_join'):
        return _ODBSymbol('skip', 0.0, 0.0, desc)

    if desc.startswith('r') and not desc.startswith('rect'):
        try:
            d = float(desc[1:])
            return _ODBSymbol('round', d, d, desc)
        except ValueError: pass
    elif desc.startswith('rect'):
        try:
            parts = desc[4:].split('x')
            w = float(parts[0])
            h = float(parts[1])
            return _ODBSymbol('rect', w, h, desc)
        except (IndexError, ValueError): pass
    elif desc.startswith('donut_r'):
        try:
            parts = desc[7:].split('x')
            D = float(parts[0])
            d = float(parts[1])
            return _ODBSymbol('donut', D, d, desc)
        except (IndexError, ValueError): pass
    elif desc.startswith('oval'):
        try:
            parts = desc[4:].split('x')
            w = float(parts[0])
            h = float(parts[1])
            return _ODBSymbol('oval', w, h, desc)
        except (IndexError, ValueError): pass
    elif desc.startswith('s') and not desc.startswith('square') and not desc.startswith('special') and not desc.startswith('sc_join'):
        try:
            d = float(desc[1:])
            return _ODBSymbol('square', d, d, desc)
        except ValueError: pass
    elif desc.startswith('di'):
        try:
            d = float(desc[2:])
            return _ODBSymbol('diamond', d, d, desc)
        except ValueError: pass

    # Default fallback
    return _ODBSymbol('unknown', 0.1, 0.1, desc)


def _parse_symbol_table(lines: list) -> dict:
    """
    Parse the $ lines at the top of a features file into a symbol dict.

    Each $ line format:
        $<index> <descriptor>
    e.g.:  $0 r0.500
           $3 rect 1.200 0.500

    Returns {index: _ODBSymbol}.
    Symbol table ends at the first non-$ (and non-comment, non-blank) line.
    """
    symbols = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or stripped.startswith(';'):
            continue
        if not stripped.startswith('$'):
            break  # symbol table is always at the top; stop at first feature line

        # Strip inline comments
        stripped = stripped.split('#')[0].split(';')[0].strip()

        parts = stripped.split(None, 1)
        if len(parts) < 2:
            continue

        try:
            idx = int(parts[0][1:])  # remove '$'
        except ValueError:
            continue

        sym = _parse_symbol_descriptor(parts[1])
        symbols[idx] = sym

    return symbols


def _parse_symbol_table_from_text(text: str) -> dict:
    """Parse symbol table from full features file text."""
    return _parse_symbol_table(text.splitlines())


# ---------------------------------------------------------------------------
# Geometry builders
# ---------------------------------------------------------------------------

def _symbol_to_geometry(x: float, y: float, sym: _ODBSymbol, rotation_deg: float):
    """
    Build a Shapely geometry for a pad at (x, y) with the given symbol.

    Resolution is intentionally kept low (8-16 segments) for performance —
    PCB files often have tens of thousands of pads.
    """
    try:
        if sym.shape == 'skip':
            return None

        if sym.shape == 'round':
            radius = sym.size_x / 2.0
            if radius <= 0:
                return None
            res = 8 if radius < 0.1 else 16
            return Point(x, y).buffer(radius, resolution=res)

        elif sym.shape == 'square':
            s = sym.size_x
            geom = shapely_box(x - s/2, y - s/2, x + s/2, y + s/2)

        elif sym.shape == 'rect':
            w, h = sym.size_x, sym.size_y
            geom = shapely_box(x - w/2, y - h/2, x + w/2, y + h/2)

        elif sym.shape == 'oval':
            # Obround: rectangle with semicircular ends
            w, h = sym.size_x, sym.size_y
            r = min(w, h) / 2.0
            rect = shapely_box(x - w/2, y - h/2, x + w/2, y + h/2)
            geom = rect.buffer(r * 0.01).simplify(0.001)  # cosmetic rounding

        elif sym.shape == 'diamond':
            s = sym.size_x / 2.0
            pts = [(x, y + s), (x + s, y), (x, y - s), (x - s, y)]
            geom = Polygon(pts)

        elif sym.shape == 'donut':
            outer = Point(x, y).buffer(sym.size_x / 2.0, resolution=16)
            inner = Point(x, y).buffer(sym.size_y / 2.0, resolution=16)
            geom = outer.difference(inner)

        else:
            # Fallback for unsupported shapes: bounding circle
            radius = max(sym.size_x, sym.size_y) / 2.0
            return Point(x, y).buffer(max(radius, 0.01), resolution=8)

        # Apply rotation if significant
        if abs(rotation_deg) > 0.01:
            geom = shapely_rotate(geom, rotation_deg, origin=(x, y))

        return geom

    except Exception:
        return None


def _odb_arc_to_points(x1: float, y1: float, x2: float, y2: float,
                        xc: float, yc: float, num_segments: int = 16) -> list:
    """
    Approximate a circular arc (start→end around center) with line points.

    ODB++ arc contours are counterclockwise by default. We compute the CCW
    sweep angle and sample evenly along it.
    """
    r = math.sqrt((x1 - xc)**2 + (y1 - yc)**2)
    if r < 1e-9:
        return [(x2, y2)]

    a_start = math.atan2(y1 - yc, x1 - xc)
    a_end   = math.atan2(y2 - yc, x2 - xc)

    # Ensure CCW sweep (positive direction)
    if a_end <= a_start:
        a_end += 2 * math.pi

    sweep = a_end - a_start
    n = max(4, int(abs(sweep) / (2 * math.pi) * num_segments))

    pts = []
    for k in range(1, n + 1):
        a = a_start + sweep * k / n
        pts.append((xc + r * math.cos(a), yc + r * math.sin(a)))

    return pts


# ---------------------------------------------------------------------------
# Feature record parsers
# ---------------------------------------------------------------------------

def _parse_pad_record(parts: list, symbols: dict, uf: float,
                       force_positive: bool = False,
                       ignore_polarity: bool = False):
    """
    Parse a P (pad/flash) or H (drill hole) record.

    Format: P x y sym_idx rotation mirror polarity [;attrs]
    polarity: P=positive (render), N=negative (clearance)
    force_positive: H records are always positive regardless of field value
    ignore_polarity: return geometry even for N records (used for neg-polarity cutouts)

    Returns (geometry, polarity_char) tuple.
    """
    try:
        x   = float(parts[1]) * uf
        y   = float(parts[2]) * uf
        sym_idx = int(parts[3])
        rot = float(parts[4]) if len(parts) > 4 else 0.0
        polarity = parts[6].upper() if len(parts) > 6 else 'P'
    except (IndexError, ValueError):
        return None, 'P'

    if force_positive:
        polarity = 'P'

    sym = symbols.get(sym_idx)
    if sym is None:
        return None, polarity

    if polarity == 'N' and not ignore_polarity:
        return None, 'N'

    return _symbol_to_geometry(x, y, sym, rot), polarity


def _parse_line_record(parts: list, symbols: dict, uf: float,
                        ignore_polarity: bool = False):
    """
    Parse an L (line/trace) record.

    Format: L x1 y1 x2 y2 sym_idx polarity [;attrs]
    Line width is the symbol's size_x.
    ignore_polarity: return geometry even for N records (used for neg-polarity cutouts)

    Returns (geometry, polarity_char) tuple.
    """
    try:
        x1  = float(parts[1]) * uf
        y1  = float(parts[2]) * uf
        x2  = float(parts[3]) * uf
        y2  = float(parts[4]) * uf
        sym_idx = int(parts[5])
        polarity = parts[6].upper() if len(parts) > 6 else 'P'
    except (IndexError, ValueError):
        return None, 'P'

    if polarity == 'N' and not ignore_polarity:
        return None, 'N'

    sym = symbols.get(sym_idx)
    if sym is None:
        return None, polarity

    width = sym.size_x
    if width <= 0:
        return None, polarity

    try:
        if abs(x2 - x1) < 1e-9 and abs(y2 - y1) < 1e-9:
            return Point(x1, y1).buffer(width / 2.0, resolution=8), polarity
        line = LineString([(x1, y1), (x2, y2)])
        cap = 1 if sym.shape == 'round' else 2  # 1=round, 2=flat caps
        return line.buffer(width / 2.0, cap_style=cap, resolution=8), polarity
    except Exception:
        return None, polarity


def _parse_surface_block(surface_lines: list, uf: float):
    """
    Parse the body of an S..SE surface block into a Shapely Polygon.

    OB x y I  — begin outer or inner contour (I = initial point)
    OS x y    — line segment to (x, y)
    OC x y xc yc — arc to (x,y) with center (xc,yc)
    OE        — end contour

    First contour = exterior ring; subsequent contours = holes.
    """
    contours = []
    current: list = []

    for line in surface_lines:
        line = line.split(';')[0].strip()
        if not line or line.startswith('#'):
            continue

        parts = line.split()
        if not parts:
            continue
        cmd = parts[0].upper()

        if cmd == 'OB':
            current = []
            if len(parts) >= 3:
                x, y = float(parts[1]) * uf, float(parts[2]) * uf
                current.append((x, y))

        elif cmd == 'OS':
            if len(parts) >= 3:
                x, y = float(parts[1]) * uf, float(parts[2]) * uf
                current.append((x, y))

        elif cmd == 'OC':
            # Arc: OC x_end y_end x_center y_center
            if len(parts) >= 5 and current:
                x_end = float(parts[1]) * uf
                y_end = float(parts[2]) * uf
                xc    = float(parts[3]) * uf
                yc    = float(parts[4]) * uf
                x_start, y_start = current[-1]
                arc_pts = _odb_arc_to_points(x_start, y_start, x_end, y_end, xc, yc)
                current.extend(arc_pts)

        elif cmd == 'OE':
            if len(current) >= 3:
                contours.append(current)
            current = []

    if not contours:
        return None

    try:
        exterior = contours[0]
        holes    = contours[1:]
        poly = Polygon(exterior, holes)
        if not poly.is_valid:
            poly = poly.buffer(0)  # standard Shapely fix for self-intersections
        return poly if not poly.is_empty else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Full features file parser
# ---------------------------------------------------------------------------

def _parse_features_text(text: str, uf: float, unknown_symbols: set) -> tuple:
    """
    Parse a full ODB++ features file text into Shapely geometries.

    Returns (geometries, trace_widths, warnings, fiducials, drill_hits).
    drill_hits is a list of (x, y, diameter_mm) tuples from H records.
    """
    pos_geoms = []     # positive-polarity geometries (add copper)
    neg_geoms = []     # negative-polarity geometries (clearances to subtract)
    trace_widths = []  # parallel to pos_geoms: feature width in mm
    warnings = []
    fiducials = []
    drill_hits = []   # list of (x, y, diameter_mm) from H (drill hole) records
    lines = text.splitlines()

    # Parse symbol table first (all $ lines before first feature)
    symbols = _parse_symbol_table(lines)

    for sym in symbols.values():
        if sym.shape == 'unknown' or sym.shape == 'skip':
            unknown_symbols.add(sym.raw_desc)

    # Scale aperture sizes from native units → mm.
    # Symbol descriptors like '$0 r0.050' store sizes in the job's native unit
    # (inches here), while we multiply coordinates by uf below.  Both must be
    # in the same unit or pads/traces will be microscopic / gigantic.
    if uf != 1.0:
        for sym in symbols.values():
            sym.size_x *= uf
            sym.size_y *= uf

    # Find where feature records start (first non-comment, non-$, non-blank line)
    feature_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith('$') \
                    and not stripped.startswith('#') \
                    and not stripped.startswith(';') \
                    and not stripped.upper().startswith('UNITS') \
                    and not stripped.upper().startswith('ID='):
            feature_start = i
            break

    i = feature_start
    error_count = 0
    MAX_ERRORS = 50  # stop accumulating warnings after this many

    while i < len(lines):
        raw_line = lines[i].strip()
        i += 1

        if not raw_line or raw_line.startswith('#') or raw_line.startswith(';'):
            continue

        is_fiducial = '.fiducial' in raw_line.lower()

        # Strip trailing attribute section
        line = raw_line.split(';')[0].strip()
        if not line:
            continue

        parts = line.split()
        record_type = parts[0].upper()

        try:
            if record_type in ('P', 'H'):
                if is_fiducial and len(parts) >= 3:
                    try:
                        fx = float(parts[1]) * uf
                        fy = float(parts[2]) * uf
                        fiducials.append((fx, fy))
                    except ValueError: pass

                # H = drill hole — capture position + diameter, always positive
                if record_type == 'H' and len(parts) >= 4:
                    try:
                        hx = float(parts[1]) * uf
                        hy = float(parts[2]) * uf
                        sym_idx = int(parts[3])
                        sym = symbols.get(sym_idx)
                        diam = max(sym.size_x, sym.size_y) if sym else 0.1
                        drill_hits.append((hx, hy, diam))
                    except (ValueError, IndexError):
                        pass

                geom, polarity = _parse_pad_record(
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
                geom, polarity = _parse_line_record(parts, symbols, uf,
                                                    ignore_polarity=True)
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
                # Surface polarity is on the S line: S <polarity> [;attrs]
                surf_polarity = parts[1].upper() if len(parts) > 1 else 'P'
                # Collect lines until SE
                surface_lines = []
                while i < len(lines):
                    sline = lines[i].strip()
                    i += 1
                    if sline.upper().startswith('SE'):
                        break
                    surface_lines.append(sline)
                geom = _parse_surface_block(surface_lines, uf)
                if geom is not None:
                    if surf_polarity == 'N':
                        neg_geoms.append(geom)
                    else:
                        pos_geoms.append(geom)
                        minx, miny, maxx, maxy = geom.bounds
                        trace_widths.append(max(maxx - minx, maxy - miny))

            # A (arc), T (text), B (barcode) — skip silently

        except Exception as e:
            error_count += 1
            if error_count <= MAX_ERRORS:
                warnings.append(f"Feature parse error at line {i}: {e}")

    # Apply negative-polarity subtraction (clearances cut into copper planes)
    if neg_geoms and pos_geoms:
        try:
            pos_union = unary_union(pos_geoms)
            neg_union = unary_union(neg_geoms)
            result = pos_union.difference(neg_union)
            # Decompose back to a flat geometry list for the visualizer
            if result.geom_type == 'MultiPolygon':
                geometries = list(result.geoms)
            elif result.geom_type in ('Polygon', 'GeometryCollection'):
                geometries = [g for g in (result.geoms if hasattr(result, 'geoms') else [result])
                              if not g.is_empty]
            else:
                geometries = [result] if not result.is_empty else pos_geoms
            # After union-difference, per-feature widths are no longer meaningful;
            # use 0.0 so LOD filtering leaves these large geometries alone
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

def _compute_bounds(geoms: list) -> tuple:
    """Compute (xmin, ymin, xmax, ymax) from a list of Shapely geometries."""
    if not geoms:
        return (0.0, 0.0, 0.0, 0.0)
    try:
        b = unary_union(geoms).bounds
        return (b[0], b[1], b[2], b[3])
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)


def _aggregate_bounds(layers: dict) -> tuple:
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


# ---------------------------------------------------------------------------
# Profile (board outline) parser
# ---------------------------------------------------------------------------

def _parse_profile_layer(job_root: str, step_name: str, uf: float, unknown_symbols: set) -> Optional[ODBLayer]:
    """
    Parse the step-level board outline from steps/<step>/profile.

    This file uses the same features format as layer features files.
    """
    profile_path = os.path.join(job_root, 'steps', step_name, 'profile')

    text = _read_features_text(profile_path)
    if text is None:
        text = _read_features_text(profile_path + '.Z')
    if text is None:
        return None

    geoms, widths, warnings, _, _drill = _parse_features_text(text, uf, unknown_symbols)
    if not geoms:
        return None

    bounds = _compute_bounds(geoms)
    return ODBLayer(
        name='profile',
        layer_type='outline',
        polygons=geoms,
        bounds=bounds,
        polygon_count=len(geoms),
        trace_widths=widths,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Component placement parser
# ---------------------------------------------------------------------------

def _parse_components(job_root: str, step_name: str, uf: float) -> list:
    """
    Parse component placement data from ODB++ steps/<step>/components file.

    ODB++ component file format:
        TOP
        CMP <idx> <x> <y> <rotation> <mirror> <refdes> <part_type> [;attrs]
        ...
        BOT
        CMP ...

    Returns list of ComponentPlacement.
    Mirror field: 'N' = top (not mirrored), 'M' or 'Y' = bottom (mirrored).
    The 'TOP'/'BOT' section header also sets current_side.
    """
    placements = []

    # ODB++ components can be at two locations:
    candidates = [
        os.path.join(job_root, 'steps', step_name, 'components'),
        os.path.join(job_root, 'steps', step_name, 'comp+top'),
        os.path.join(job_root, 'steps', step_name, 'comp+bot'),
    ]
    # Also check for component-type layers inside steps/<step>/layers/
    layers_dir = os.path.join(job_root, 'steps', step_name, 'layers')
    if os.path.isdir(layers_dir):
        for entry in os.listdir(layers_dir):
            if re.search(r'comp|component', entry, re.I):
                comp_path = os.path.join(layers_dir, entry, 'components')
                if os.path.isfile(comp_path):
                    candidates.append(comp_path)

    for comp_path in candidates:
        if not os.path.isfile(comp_path):
            continue
        try:
            with open(comp_path, 'r', errors='ignore') as f:
                content = f.read()
        except (OSError, IOError):
            continue

        current_side = 'T'
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            upper = line.upper()
            if upper == 'TOP':
                current_side = 'T'
                continue
            elif upper == 'BOT':
                current_side = 'B'
                continue

            # Strip attribute section
            line = line.split(';')[0].strip()
            parts = line.split()

            if not parts or parts[0].upper() != 'CMP':
                continue
            # CMP <idx> <x> <y> <rotation> <mirror> <refdes> <part_type>
            if len(parts) < 7:
                continue

            try:
                x = float(parts[2]) * uf
                y = float(parts[3]) * uf
                rotation = float(parts[4])
                mirror = parts[5].upper() in ('M', 'Y', '1')
                refdes = parts[6]
                part_type = parts[7] if len(parts) > 7 else ''
                side = 'B' if mirror else current_side

                placements.append(ComponentPlacement(
                    refdes=refdes,
                    part_type=part_type,
                    x=x, y=y,
                    rotation=rotation,
                    mirror=mirror,
                    side=side,
                ))
            except (ValueError, IndexError):
                continue

        # Stop after first successfully parsed file
        if placements:
            break

    return placements


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_odb_archive(data: bytes, filename: str = '') -> ParsedODB:
    """
    Parse an ODB++ .tgz archive into a ParsedODB object.

    Args:
        data:     raw bytes of the .tgz file
        filename: original filename (used only in error messages)

    Returns:
        ParsedODB with layers dict and board_bounds in mm

    Raises:
        ValueError if the archive cannot be opened
    """
    global_warnings = []
    layers = {}

    # 1. Extract archive to temp directory
    try:
        tmp_dir, job_root = _extract_odb_tgz(data)
    except Exception as e:
        raise ValueError(f"Cannot open ODB++ archive '{filename}': {e}") from e

    try:
        # 2. Detect units
        units = _read_units(job_root)
        # units factor: multiply ODB++ coordinates by this to get mm
        uf = 25.4 if units == 'inch' else 1.0

        origin_x, origin_y = _read_design_origin(job_root, uf)
        unknown_symbols = set()
        all_fiducials = []
        all_drill_hits = []

        # 3. Find step directory (step names are custom — don't assume 'pcb')
        step_name = _find_step(job_root)
        layers_dir = os.path.join(job_root, 'steps', step_name, 'layers')

        # 4. Parse layer list from matrix (block format)
        matrix_layers = _parse_matrix(job_root)
        if not matrix_layers:
            global_warnings.append(
                "matrix/matrix not found or empty — scanning layers directory"
            )
            matrix_layers = _scan_layers_dir(layers_dir)

        # 5. Parse each layer's features file
        # Area-definition layers (FLEX_AREA, BEND_AREA, RIGID_AREA) are large
        # single filled polygons that define board zones, not circuit geometry.
        # They completely obscure copper layers when rendered, so skip them.
        _AREA_LAYER = re.compile(
            r'flex.?area|bend.?area|rigid.?area|board.?area|panel.?area',
            re.IGNORECASE,
        )

        for layer_name, layer_type in matrix_layers:
            # Skip non-renderable layers early to save time
            if layer_type == 'other':
                continue
            if _AREA_LAYER.search(layer_name):
                continue

            features_path = os.path.join(layers_dir, layer_name, 'features')

            text = _read_features_text(features_path)
            if text is None:
                text = _read_features_text(features_path + '.Z')
            if text is None:
                global_warnings.append(
                    f"Layer '{layer_name}': features file not found, skipping"
                )
                continue

            # Allow features file header to override units (secondary source)
            file_units = _units_from_text(text)
            layer_uf = (25.4 if file_units == 'inch' else 1.0) if file_units else uf

            try:
                geoms, widths, layer_warnings, fiducials, layer_drill_hits = _parse_features_text(text, layer_uf, unknown_symbols)
                all_fiducials.extend(fiducials)
                if layer_type == 'drill':
                    for hx, hy, diam in layer_drill_hits:
                        all_drill_hits.append(DrillHit(x=hx, y=hy, diameter=diam, layer_name=layer_name))
            except Exception as e:
                global_warnings.append(
                    f"Layer '{layer_name}': parse error — {e}, skipping"
                )
                continue

            if not geoms:
                global_warnings.append(
                    f"Layer '{layer_name}': no geometry parsed"
                )
                continue

            bounds = _compute_bounds(geoms)
            layers[layer_name] = ODBLayer(
                name=layer_name,
                layer_type=layer_type,
                polygons=geoms,
                bounds=bounds,
                polygon_count=len(geoms),
                trace_widths=widths,
                warnings=layer_warnings,
            )

        # 6. Parse board profile (outline)
        profile_layer = _parse_profile_layer(job_root, step_name, uf, unknown_symbols)
        if profile_layer is not None:
            layers['profile'] = profile_layer
        else:
            global_warnings.append("Board profile not found — outline layer unavailable")

        # 7. Parse component placements
        all_components = _parse_components(job_root, step_name, uf)

        # 8. Compute aggregate board bounds
        board_bounds = _aggregate_bounds(layers)

        return ParsedODB(
            layers=layers,
            board_bounds=board_bounds,
            step_name=step_name,
            units=units,
            origin_x=origin_x,
            origin_y=origin_y,
            unknown_symbols=unknown_symbols,
            fiducials=all_fiducials,
            drill_hits=all_drill_hits,
            components=all_components,
            warnings=global_warnings,
        )

    finally:
        # Always remove temp directory regardless of success/failure
        shutil.rmtree(tmp_dir, ignore_errors=True)
