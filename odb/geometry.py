"""odb/geometry.py — Shapely geometry builders for ODB++ features."""

import math

from shapely.geometry import Point, Polygon, LineString
from shapely.geometry import box as shapely_box
from shapely.affinity import rotate as shapely_rotate, scale as shapely_scale

from odb.constants import (
    ARC_SEGMENTS_COARSE, ARC_SEGMENTS_FINE,
    COORD_EPSILON, MIN_FEATURE_SIZE,
    SPOKE_WIDTH_FACTOR, CROSS_ARM_FACTOR,
)
from odb.models import _ODBSymbol


# ---------------------------------------------------------------------------
# Shape handlers (one function per shape type)
# ---------------------------------------------------------------------------

def _make_circle(sym: _ODBSymbol, x: float, y: float) -> object:
    radius = sym.size_x / 2.0
    if radius <= 0:
        return None
    res = ARC_SEGMENTS_COARSE if radius < 0.5 else ARC_SEGMENTS_FINE
    return Point(x, y).buffer(radius, resolution=res)


def _make_square(sym: _ODBSymbol, x: float, y: float) -> object:
    s = sym.size_x
    return shapely_box(x - s/2, y - s/2, x + s/2, y + s/2)


def _make_rect(sym: _ODBSymbol, x: float, y: float) -> object:
    w, h = sym.size_x, sym.size_y
    return shapely_box(x - w/2, y - h/2, x + w/2, y + h/2)


def _make_oval(sym: _ODBSymbol, x: float, y: float) -> object:
    w, h = sym.size_x, sym.size_y
    r = min(w, h) / 2.0
    if w >= h:
        rect = shapely_box(x - (w/2 - r), y - r, x + (w/2 - r), y + r)
        c1 = Point(x - (w/2 - r), y).buffer(r, resolution=ARC_SEGMENTS_COARSE)
        c2 = Point(x + (w/2 - r), y).buffer(r, resolution=ARC_SEGMENTS_COARSE)
    else:
        rect = shapely_box(x - r, y - (h/2 - r), x + r, y + (h/2 - r))
        c1 = Point(x, y - (h/2 - r)).buffer(r, resolution=ARC_SEGMENTS_COARSE)
        c2 = Point(x, y + (h/2 - r)).buffer(r, resolution=ARC_SEGMENTS_COARSE)
    return rect.union(c1).union(c2)


def _make_rounded_rect(sym: _ODBSymbol, x: float, y: float) -> object:
    w, h = sym.size_x, sym.size_y
    r = min(sym.corner_r, min(w, h) / 2.0)
    if r <= 0:
        return shapely_box(x - w/2, y - h/2, x + w/2, y + h/2)
    inner = shapely_box(x - w/2 + r, y - h/2 + r, x + w/2 - r, y + h/2 - r)
    return inner.buffer(r, resolution=4)


def _make_diamond(sym: _ODBSymbol, x: float, y: float) -> object:
    s = sym.size_x / 2.0
    return Polygon([(x, y + s), (x + s, y), (x, y - s), (x - s, y)])


def _make_octagon(sym: _ODBSymbol, x: float, y: float) -> object:
    d = sym.size_x / 2.0
    pts = [(x + d * math.cos(math.pi / 8 + i * math.pi / 4),
            y + d * math.sin(math.pi / 8 + i * math.pi / 4))
           for i in range(8)]
    return Polygon(pts)


def _make_hexagon(sym: _ODBSymbol, x: float, y: float) -> object:
    d = sym.size_x / 2.0
    pts = [(x + d * math.cos(i * math.pi / 3),
            y + d * math.sin(i * math.pi / 3))
           for i in range(6)]
    return Polygon(pts)


def _make_triangle(sym: _ODBSymbol, x: float, y: float) -> object:
    d = sym.size_x / 2.0
    pts = [(x + d * math.cos(math.pi / 2 + i * 2 * math.pi / 3),
            y + d * math.sin(math.pi / 2 + i * 2 * math.pi / 3))
           for i in range(3)]
    return Polygon(pts)


def _make_ellipse(sym: _ODBSymbol, x: float, y: float) -> object:
    w, h = sym.size_x, sym.size_y
    circle = Point(x, y).buffer(0.5, resolution=ARC_SEGMENTS_FINE)
    return shapely_scale(circle, w, h, origin=(x, y))


def _make_thermal(sym: _ODBSymbol, x: float, y: float) -> object:
    outer = Point(x, y).buffer(sym.size_x / 2.0, resolution=ARC_SEGMENTS_FINE)
    inner = Point(x, y).buffer(sym.size_y / 2.0, resolution=ARC_SEGMENTS_FINE)
    ring = outer.difference(inner)
    spoke_w = max((sym.size_x - sym.size_y) * SPOKE_WIDTH_FACTOR, MIN_FEATURE_SIZE)
    h_spoke = shapely_box(x - sym.size_x/2, y - spoke_w/2, x + sym.size_x/2, y + spoke_w/2)
    v_spoke = shapely_box(x - spoke_w/2, y - sym.size_x/2, x + spoke_w/2, y + sym.size_x/2)
    return ring.difference(h_spoke).difference(v_spoke)


def _make_cross(sym: _ODBSymbol, x: float, y: float) -> object:
    w, h = sym.size_x, sym.size_y
    arm_w = min(w, h) * CROSS_ARM_FACTOR
    h_bar = shapely_box(x - w/2, y - arm_w/2, x + w/2, y + arm_w/2)
    v_bar = shapely_box(x - arm_w/2, y - h/2, x + arm_w/2, y + h/2)
    return h_bar.union(v_bar)


def _make_donut(sym: _ODBSymbol, x: float, y: float) -> object:
    outer = Point(x, y).buffer(sym.size_x / 2.0, resolution=ARC_SEGMENTS_FINE)
    inner = Point(x, y).buffer(sym.size_y / 2.0, resolution=ARC_SEGMENTS_FINE)
    return outer.difference(inner)


def _make_fallback(sym: _ODBSymbol, x: float, y: float) -> object:
    radius = max(sym.size_x, sym.size_y) / 2.0
    return Point(x, y).buffer(max(radius, MIN_FEATURE_SIZE / 2), resolution=ARC_SEGMENTS_COARSE)


# Dispatch table: shape name → handler function
_SHAPE_HANDLERS = {
    'round':        _make_circle,
    'square':       _make_square,
    'rect':         _make_rect,
    'oval':         _make_oval,
    'rounded_rect': _make_rounded_rect,
    'diamond':      _make_diamond,
    'octagon':      _make_octagon,
    'hexagon':      _make_hexagon,
    'triangle':     _make_triangle,
    'ellipse':      _make_ellipse,
    'thermal':      _make_thermal,
    'cross':        _make_cross,
    'donut':        _make_donut,
}


# ---------------------------------------------------------------------------
# Public geometry builders
# ---------------------------------------------------------------------------

def symbol_to_geometry(x: float, y: float, sym: _ODBSymbol, rotation_deg: float):
    """
    Build a Shapely geometry for a pad at (x, y) with the given symbol.

    Resolution is kept low (8–16 segments) for performance — PCB files
    often have tens of thousands of pads.
    """
    try:
        if sym.shape == 'skip':
            return None

        handler = _SHAPE_HANDLERS.get(sym.shape, _make_fallback)
        geom = handler(sym, x, y)

        if geom is not None and abs(rotation_deg) > 0.01:
            geom = shapely_rotate(geom, rotation_deg, origin=(x, y))

        return geom

    except Exception:
        return None


def arc_to_points(x1: float, y1: float, x2: float, y2: float,
                  xc: float, yc: float,
                  cw: bool = False,
                  num_segments: int = ARC_SEGMENTS_FINE,
                  radius: float = None) -> list:
    """
    Approximate a circular arc (start → end around center) with line points.

    cw=False → counter-clockwise (ODB++ default).
    cw=True  → clockwise (when OC line has Y/CW flag).
    radius: when provided, use adaptive segment count (one segment per 5 µm of
            arc length, clamped 16–512) instead of num_segments.
    """
    r = math.sqrt((x1 - xc)**2 + (y1 - yc)**2) if radius is None else radius
    if r < COORD_EPSILON:
        return [(x2, y2)]

    a_start = math.atan2(y1 - yc, x1 - xc)
    a_end   = math.atan2(y2 - yc, x2 - xc)

    if cw:
        if a_end >= a_start:
            a_end -= 2 * math.pi
    else:
        if a_end <= a_start:
            a_end += 2 * math.pi

    sweep = a_end - a_start
    if radius is not None:
        n = max(16, min(512, int(abs(sweep) * r / 0.005)))
    else:
        n = max(ARC_SEGMENTS_COARSE, int(abs(sweep) / (2 * math.pi) * num_segments))

    return [
        (xc + r * math.cos(a_start + sweep * k / n),
         yc + r * math.sin(a_start + sweep * k / n))
        for k in range(1, n + 1)
    ]


def parse_pad_record(parts: list, symbols: dict, uf: float,
                     force_positive: bool = False,
                     ignore_polarity: bool = False):
    """
    Parse a P (pad/flash) or H (drill hole) record.

    ODB++ P record field order varies by exporter:
      Format A (standard):  P x y sym rot mirror polarity
      Format B (InCAM Pro): P x y sym polarity rot mirror
    Detected by checking whether parts[4] is a polarity token (P/N) or a number.

    Returns (geometry, polarity_char) tuple.
    """
    try:
        x       = float(parts[1]) * uf
        y       = float(parts[2]) * uf
        sym_idx = int(parts[3])

        p4 = parts[4].split(';')[0].strip().upper() if len(parts) > 4 else ''
        if p4 in ('P', 'N'):
            polarity = p4
            rot = float(parts[5].split(';')[0]) if len(parts) > 5 else 0.0
        else:
            rot      = float(p4) if p4 else 0.0
            polarity = parts[6].split(';')[0].strip().upper() if len(parts) > 6 else 'P'
    except (IndexError, ValueError):
        return None, 'P'

    if force_positive:
        polarity = 'P'

    sym = symbols.get(sym_idx)
    if sym is None:
        return None, polarity

    if polarity == 'N' and not ignore_polarity:
        return None, 'N'

    return symbol_to_geometry(x, y, sym, rot), polarity


def parse_line_record(parts: list, symbols: dict, uf: float,
                      ignore_polarity: bool = False):
    """
    Parse an L (line/trace) record.

    Format: L x1 y1 x2 y2 sym_idx polarity
    Line width is the symbol's size_x.

    Returns (geometry, polarity_char) tuple.
    """
    try:
        x1      = float(parts[1]) * uf
        y1      = float(parts[2]) * uf
        x2      = float(parts[3]) * uf
        y2      = float(parts[4]) * uf
        sym_idx = int(parts[5])
        polarity = parts[6].upper() if len(parts) > 6 else 'P'
    except (IndexError, ValueError):
        return None, 'P'

    if polarity == 'N' and not ignore_polarity:
        return None, 'N'

    sym = symbols.get(sym_idx)
    if sym is None or sym.size_x <= 0:
        return None, polarity

    try:
        if abs(x2 - x1) < COORD_EPSILON and abs(y2 - y1) < COORD_EPSILON:
            return Point(x1, y1).buffer(sym.size_x / 2.0, resolution=ARC_SEGMENTS_COARSE), polarity
        line = LineString([(x1, y1), (x2, y2)])
        cap = 1 if sym.shape == 'round' else 2  # 1=round, 2=flat caps
        return line.buffer(sym.size_x / 2.0, cap_style=cap, resolution=ARC_SEGMENTS_COARSE), polarity
    except Exception:
        return None, polarity


def parse_arc_record(parts: list, symbols: dict, uf: float,
                    ignore_polarity: bool = False):
    """
    Parse an A (arc trace) record.

    ODB++ A record field order:
      A xs ys xe ye xc yc  sym_idx  polarity  [mirror]  [cw_flag]

    Returns (geometry, polarity_char) tuple.
    """
    try:
        x1       = float(parts[1]) * uf
        y1       = float(parts[2]) * uf
        x2       = float(parts[3]) * uf
        y2       = float(parts[4]) * uf
        xc_      = float(parts[5]) * uf
        yc_      = float(parts[6]) * uf
        sym_idx  = int(parts[7])
        polarity = parts[8].split(';')[0].strip().upper() if len(parts) > 8 else 'P'
        cw_flag  = False
        if len(parts) > 10:
            d = parts[10].split(';')[0].strip().upper()
            cw_flag = d in ('Y', 'CW', '1')
    except (IndexError, ValueError):
        return None, 'P'

    if polarity == 'N' and not ignore_polarity:
        return None, 'N'

    sym = symbols.get(sym_idx)
    if sym is None or sym.size_x <= 0:
        return None, polarity

    try:
        r = math.sqrt((x1 - xc_)**2 + (y1 - yc_)**2)
        pts = arc_to_points(x1, y1, x2, y2, xc_, yc_, cw=cw_flag, radius=r)
        all_pts = [(x1, y1)] + pts
        if len(all_pts) < 2:
            return None, polarity
        line = LineString(all_pts)
        cap = 1 if sym.shape == 'round' else 2
        return line.buffer(sym.size_x / 2.0, cap_style=cap,
                           resolution=ARC_SEGMENTS_COARSE), polarity
    except Exception:
        return None, polarity


def parse_surface_block(surface_lines: list, uf: float):
    """
    Parse the body of an S..SE surface block into a Shapely Polygon.

    OB x y  — begin contour
    OS x y  — line segment
    OC x y xc yc [cw_flag] — arc segment
    OE      — end contour

    First contour = exterior ring; subsequent contours = holes.
    """
    contours = []
    current = []

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
                current.append((float(parts[1]) * uf, float(parts[2]) * uf))

        elif cmd == 'OS':
            if len(parts) >= 3:
                current.append((float(parts[1]) * uf, float(parts[2]) * uf))

        elif cmd == 'OC':
            if len(parts) >= 5 and current:
                x_end = float(parts[1]) * uf
                y_end = float(parts[2]) * uf
                xc    = float(parts[3]) * uf
                yc    = float(parts[4]) * uf
                cw_flag = False
                if len(parts) >= 6:
                    d = parts[5].split(';')[0].strip().upper()
                    cw_flag = d in ('Y', 'CW', '1')
                x_start, y_start = current[-1]
                r_oc = math.sqrt((x_start - xc)**2 + (y_start - yc)**2)
                current.extend(arc_to_points(x_start, y_start, x_end, y_end, xc, yc,
                                             cw=cw_flag, radius=r_oc))

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
            poly = poly.buffer(0)
        return poly if not poly.is_empty else None
    except Exception:
        return None
