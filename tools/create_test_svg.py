#!/usr/bin/env python3
"""
create_test_svg.py — Generates realistic PCB unit cell SVGs for testing
the SVG dual-mode rendering feature.

Produces: test_svgs/BU-01_F.svg, BU-01_B.svg, BU-02_F.svg, BU-02_B.svg

Each SVG layer includes:
  - Dark substrate background
  - Copper pour (flood fill polygon)
  - Horizontal, vertical, and 45-degree diagonal traces
  - L-shaped routed connections between IC pads
  - Via pads with annular ring + drill hole
  - Through-hole pads (annular ring + drill)
  - SMD pads in IC footprint grid patterns
  - Edge connector pads
  - Soldermask layer (semi-transparent green with pad openings)
  - Silkscreen reference designators and component outlines
  - viewBox in mm for auto-detection
"""

import math
import os
import random

CELL_W_MM = 35.0
CELL_H_MM = 39.0

random.seed(42)


# ---------------------------------------------------------------------------
# Primitive SVG builders
# ---------------------------------------------------------------------------

def _trace(x1, y1, x2, y2, width=0.15, color="#CC6600"):
    return (
        f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
        f'stroke="{color}" stroke-width="{width:.3f}" stroke-linecap="round"/>\n'
    )


def _path(d, stroke="#CC6600", width=0.15, fill="none"):
    return (
        f'<path d="{d}" stroke="{stroke}" stroke-width="{width:.3f}" '
        f'fill="{fill}" stroke-linecap="round" stroke-linejoin="round"/>\n'
    )


def _via(cx, cy, drill_r=0.175, annular_r=0.35, ring_r=0.55, accent="#CC6600"):
    """Via: annular ring copper + drill hole."""
    return (
        # Copper annular fill
        f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{annular_r:.3f}" fill="{accent}" opacity="0.9"/>\n'
        # Annular ring outline
        f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{ring_r:.3f}" fill="none" '
        f'stroke="{accent}" stroke-width="0.12"/>\n'
        # Drill hole (dark)
        f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{drill_r:.3f}" fill="#111111"/>\n'
    )


def _through_hole_pad(cx, cy, drill_r=0.3, pad_r=0.55, accent="#CC6600"):
    """Through-hole pad: larger annular ring."""
    return (
        f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{pad_r:.3f}" fill="{accent}" opacity="0.85"/>\n'
        f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{pad_r:.3f}" fill="none" '
        f'stroke="{accent}" stroke-width="0.08"/>\n'
        f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{drill_r:.3f}" fill="#111111"/>\n'
    )


def _smd_pad(x, y, w=0.6, h=0.4, color="#CC6600"):
    return (
        f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
        f'fill="{color}" opacity="0.9" rx="0.04" ry="0.04"/>\n'
    )


def _soldermask_opening(cx, cy, r):
    """Circle cutout in soldermask (rendered as white circle to represent opening)."""
    return f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" fill="#1A3A1A"/>\n'


def _soldermask_rect_opening(x, y, w, h):
    return (
        f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
        f'fill="#1A3A1A" rx="0.04" ry="0.04"/>\n'
    )


def _silk_text(x, y, content, size=1.0):
    return (
        f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size:.2f}" fill="#EEEECC" '
        f'font-family="monospace" opacity="0.85">{content}</text>\n'
    )


def _silk_rect(x, y, w, h, r=0.0):
    corner = f' rx="{r}" ry="{r}"' if r else ''
    return (
        f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
        f'fill="none" stroke="#EEEECC" stroke-width="0.08" opacity="0.7"{corner}/>\n'
    )


# ---------------------------------------------------------------------------
# Copper pour (flood fill polygon)
# ---------------------------------------------------------------------------

def _copper_pour(w, h, accent, rng):
    """Irregular copper pour polygon covering ~60% of the cell."""
    # Build a rough polygon that avoids the very edges
    pts = [
        (rng.uniform(2, 5), rng.uniform(2, 5)),
        (rng.uniform(w * 0.4, w * 0.6), rng.uniform(1, 3)),
        (rng.uniform(w - 5, w - 2), rng.uniform(2, 5)),
        (rng.uniform(w - 4, w - 2), rng.uniform(h * 0.4, h * 0.6)),
        (rng.uniform(w - 5, w - 2), rng.uniform(h - 5, h - 2)),
        (rng.uniform(w * 0.4, w * 0.6), rng.uniform(h - 3, h - 1)),
        (rng.uniform(2, 5), rng.uniform(h - 5, h - 2)),
        (rng.uniform(1, 3), rng.uniform(h * 0.4, h * 0.6)),
    ]
    pts_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
    return (
        f'<polygon points="{pts_str}" fill="{accent}" opacity="0.18" '
        f'stroke="{accent}" stroke-width="0.06" stroke-opacity="0.4"/>\n'
    )


# ---------------------------------------------------------------------------
# IC footprint (QFP-style, pads on all 4 sides)
# ---------------------------------------------------------------------------

def _ic_footprint(cx, cy, n_pads_side=6, pitch=0.8, pad_w=0.5, pad_h=0.25,
                  body_size=3.5, accent="#CC6600", refdes="U1"):
    """QFP-style IC: pads on all 4 sides + body outline + ref text."""
    parts = []
    half = (n_pads_side - 1) * pitch / 2

    # Top pads
    for i in range(n_pads_side):
        px = cx - half + i * pitch
        py = cy - body_size / 2 - pad_h
        parts.append(_smd_pad(px - pad_w / 2, py, pad_w, pad_h, accent))

    # Bottom pads
    for i in range(n_pads_side):
        px = cx - half + i * pitch
        py = cy + body_size / 2
        parts.append(_smd_pad(px - pad_w / 2, py, pad_w, pad_h, accent))

    # Left pads
    for i in range(n_pads_side):
        py = cy - half + i * pitch
        px = cx - body_size / 2 - pad_h
        parts.append(_smd_pad(px, py - pad_w / 2, pad_h, pad_w, accent))

    # Right pads
    for i in range(n_pads_side):
        py = cy - half + i * pitch
        px = cx + body_size / 2
        parts.append(_smd_pad(px, py - pad_w / 2, pad_h, pad_w, accent))

    # Body silkscreen outline
    half_b = body_size / 2
    parts.append(_silk_rect(cx - half_b, cy - half_b, body_size, body_size, r=0.2))
    # Pin 1 marker (notch)
    parts.append(
        f'<circle cx="{cx - half_b + 0.3:.3f}" cy="{cy - half_b + 0.3:.3f}" '
        f'r="0.15" fill="#EEEECC" opacity="0.7"/>\n'
    )
    # Ref designator
    parts.append(_silk_text(cx - half_b + 0.1, cy - half_b - 0.3, refdes, size=0.8))
    return "".join(parts)


# ---------------------------------------------------------------------------
# L-shaped routing between two points
# ---------------------------------------------------------------------------

def _l_route(x1, y1, x2, y2, width=0.15, color="#CC6600", elbow_at_x=True):
    """Route a 90-degree L-shaped trace between two points."""
    if elbow_at_x:
        mid_x, mid_y = x2, y1
    else:
        mid_x, mid_y = x1, y2
    d = f"M {x1:.3f} {y1:.3f} L {mid_x:.3f} {mid_y:.3f} L {x2:.3f} {y2:.3f}"
    return _path(d, stroke=color, width=width)


def _diagonal_trace(x1, y1, length, angle_deg, width=0.12, color="#CC6600"):
    """45-degree diagonal trace segment."""
    rad = math.radians(angle_deg)
    x2 = x1 + length * math.cos(rad)
    y2 = y1 + length * math.sin(rad)
    return _trace(x1, y1, x2, y2, width=width, color=color)


# ---------------------------------------------------------------------------
# Main SVG builder
# ---------------------------------------------------------------------------

def build_unit_svg(label: str, w: float, h: float, accent: str = "#CC6600") -> str:
    rng = random.Random(hash(label) & 0xFFFFFFFF)
    parts = []

    # 1. Substrate background
    parts.append(f'<rect x="0" y="0" width="{w}" height="{h}" fill="#0F1A0F" rx="0.5" ry="0.5"/>\n')

    # 2. Copper pour (subtle flood fill)
    parts.append(_copper_pour(w, h, accent, rng))

    # 3. Outer copper keepout ring
    parts.append(
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" '
        f'fill="none" stroke="{accent}" stroke-width="0.25" rx="0.3"/>\n'
    )

    # 4. Dense horizontal traces
    for i in range(4, int(h) - 3, 3):
        y = i + rng.uniform(-0.3, 0.3)
        x1 = rng.uniform(1.0, 2.5)
        x2 = rng.uniform(w - 2.5, w - 1.0)
        parts.append(_trace(x1, y, x2, y, width=rng.choice([0.08, 0.12, 0.15, 0.2]), color=accent))

    # 5. Vertical traces
    for i in range(3, int(w) - 2, 3):
        x = i + rng.uniform(-0.3, 0.3)
        y1 = rng.uniform(1.0, 2.5)
        y2 = rng.uniform(h - 2.5, h - 1.0)
        parts.append(_trace(x, y1, x, y2, width=rng.choice([0.08, 0.12]), color=accent))

    # 6. 45-degree diagonal traces (short fanout segments)
    for _ in range(8):
        sx = rng.uniform(3, w - 3)
        sy = rng.uniform(3, h - 3)
        angle = rng.choice([45, -45, 135, -135])
        length = rng.uniform(1.5, 4.0)
        parts.append(_diagonal_trace(sx, sy, length, angle, width=0.10, color=accent))

    # 7. IC footprint (center-left)
    ic1_x, ic1_y = w * 0.28, h * 0.35
    parts.append(_ic_footprint(ic1_x, ic1_y, n_pads_side=5, pitch=0.7,
                               pad_w=0.45, pad_h=0.22, body_size=3.2,
                               accent=accent, refdes="U1"))

    # 8. Second smaller IC (center-right)
    ic2_x, ic2_y = w * 0.72, h * 0.62
    parts.append(_ic_footprint(ic2_x, ic2_y, n_pads_side=4, pitch=0.65,
                               pad_w=0.4, pad_h=0.2, body_size=2.6,
                               accent=accent, refdes="U2"))

    # 9. L-routed traces connecting IC pads to vias
    via_positions = [
        (ic1_x - 2.5, ic1_y - 3.5),
        (ic1_x + 3.0, ic1_y - 3.5),
        (ic1_x - 2.5, ic1_y + 3.5),
        (ic2_x + 2.8, ic2_y - 3.0),
        (ic2_x - 2.8, ic2_y + 3.2),
        (w * 0.5, h * 0.5),
    ]
    # Route from IC1 pads to nearby vias
    for i, (vx, vy) in enumerate(via_positions[:3]):
        src_x = ic1_x - 1.6 + i * 0.7
        src_y = ic1_y - 1.6 - 0.22  # top pad row
        parts.append(_l_route(src_x, src_y, vx, vy, width=0.12, color=accent, elbow_at_x=(i % 2 == 0)))

    # 10. Via pads
    for vx, vy in via_positions:
        parts.append(_via(vx, vy, drill_r=0.175, annular_r=0.32, ring_r=0.52, accent=accent))

    # 11. Through-hole component pads (connector row)
    th_y = h - 4.5
    for i in range(6):
        tx = 3.5 + i * 4.5
        parts.append(_through_hole_pad(tx, th_y, drill_r=0.55, pad_r=0.9, accent=accent))
    parts.append(_silk_text(3.0, th_y - 1.2, "J1", size=0.9))
    parts.append(_silk_rect(2.2, th_y - 0.95, 26.0, 1.9))

    # 12. Edge connector SMD pads (top edge)
    for i in range(8):
        ex = 2.0 + i * 3.8
        parts.append(_smd_pad(ex - 0.25, 0.6, 0.5, 0.7, color=accent))

    # 13. Soldermask layer (semi-transparent dark green, cutouts at pads)
    parts.append(
        f'<rect x="0" y="0" width="{w}" height="{h}" '
        f'fill="#1A4A1A" opacity="0.28" rx="0.5" ry="0.5"/>\n'
    )
    # Soldermask openings at vias
    for vx, vy in via_positions:
        parts.append(_soldermask_opening(vx, vy, r=0.42))
    # Openings at through-hole pads
    for i in range(6):
        tx = 3.5 + i * 4.5
        parts.append(_soldermask_opening(tx, th_y, r=1.0))
    # Openings at edge connector pads
    for i in range(8):
        ex = 2.0 + i * 3.8
        parts.append(_soldermask_rect_opening(ex - 0.3, 0.55, 0.6, 0.8))

    # 14. Label
    parts.append(_silk_text(1.0, h - 0.8, label, size=1.2))

    body = "".join(parts)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {w} {h}" width="{w}mm" height="{h}mm">\n'
        f'{body}'
        f'</svg>\n'
    )


def main():
    out_dir = "test_svgs"
    os.makedirs(out_dir, exist_ok=True)

    configs = [
        ("BU-01_F", 35.0, 39.0, "#CC6600", "BU-01 Front"),
        ("BU-01_B", 35.0, 39.0, "#3399CC", "BU-01 Back"),
        ("BU-02_F", 35.0, 39.0, "#CC3300", "BU-02 Front"),
        ("BU-02_B", 35.0, 39.0, "#33AA66", "BU-02 Back"),
    ]

    for name, w, h, accent, label in configs:
        svg = build_unit_svg(label, w, h, accent)
        path = os.path.join(out_dir, f"{name}.svg")
        with open(path, "w") as f:
            f.write(svg)
        print(f"Created: {path}  ({w}x{h} mm)")

    print(f"\nTest SVGs written to '{out_dir}/'")
    print("Upload them in the app sidebar under 'SVG Layers (Experimental)'")


if __name__ == "__main__":
    main()
