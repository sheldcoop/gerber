#!/usr/bin/env python3
"""
create_test_svg.py — Generates a realistic PCB unit cell SVG for testing
the SVG dual-mode rendering feature.

Produces: test_svgs/BU-01_F.svg  (and BU-01_B.svg, BU-02_F.svg)

The SVG represents a simplified buildup copper layer with:
  - Rectangular traces (horizontal and vertical)
  - Via pads (circles)
  - SMD pads (small rectangles)
  - viewBox in mm so auto-detection works
"""

import math
import os
import random

# Unit cell physical size (mm) — matches default sidebar values
CELL_W_MM = 35.0
CELL_H_MM = 39.0

random.seed(42)  # reproducible

def _trace(x1, y1, x2, y2, width=0.15, color="#CC6600"):
    return (
        f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
        f'stroke="{color}" stroke-width="{width:.3f}" stroke-linecap="round"/>\n'
    )

def _pad(cx, cy, r=0.3, color="#FFAA00"):
    return f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" fill="{color}" stroke="#8B4513" stroke-width="0.05"/>\n'

def _smd_pad(x, y, w=0.6, h=0.4, color="#DDAA55"):
    return (
        f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
        f'fill="{color}" stroke="#8B4513" stroke-width="0.05" rx="0.05" ry="0.05"/>\n'
    )

def _text(x, y, content, size=1.0, color="#FFFFFF"):
    return f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size}" fill="{color}" font-family="monospace">{content}</text>\n'

def build_unit_svg(label: str, w: float, h: float, accent: str = "#CC6600") -> str:
    """Build a realistic-looking PCB copper layer SVG."""
    parts = []
    # --- Background ---
    parts.append(f'<rect x="0" y="0" width="{w}" height="{h}" fill="#1A0A00" rx="0.5" ry="0.5"/>\n')
    # --- Outer copper ring ---
    parts.append(f'<rect x="0.5" y="0.5" width="{w-1}" height="{h-1}" fill="none" stroke="{accent}" stroke-width="0.3" rx="0.3"/>\n')

    # --- Horizontal traces ---
    for i in range(5, int(h) - 4, 4):
        y = i + random.uniform(-0.5, 0.5)
        x1 = random.uniform(1.0, 3.0)
        x2 = random.uniform(w - 3.0, w - 1.0)
        parts.append(_trace(x1, y, x2, y, width=random.choice([0.1, 0.15, 0.2]), color=accent))

    # --- Vertical traces ---
    for i in range(4, int(w) - 3, 4):
        x = i + random.uniform(-0.5, 0.5)
        y1 = random.uniform(1.0, 3.0)
        y2 = random.uniform(h - 3.0, h - 1.0)
        parts.append(_trace(x, y1, x, y2, width=random.choice([0.1, 0.15]), color=accent))

    # --- Via pads ---
    via_positions = [
        (w * 0.25, h * 0.25), (w * 0.75, h * 0.25),
        (w * 0.25, h * 0.75), (w * 0.75, h * 0.75),
        (w * 0.5,  h * 0.5),
    ]
    for cx, cy in via_positions:
        cx += random.uniform(-1.0, 1.0)
        cy += random.uniform(-1.0, 1.0)
        parts.append(_pad(cx, cy, r=0.35, color="#FFDD88"))
        # Annular ring
        parts.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="0.55" fill="none" stroke="{accent}" stroke-width="0.15"/>\n')

    # --- SMD pads along edges ---
    for i in range(4):
        x = 1.5 + i * 3.5
        parts.append(_smd_pad(x - 0.3, 0.8, color="#FFAA44"))
        parts.append(_smd_pad(x - 0.3, h - 1.2, color="#FFAA44"))
    for i in range(3):
        y = 3.0 + i * 5.0
        parts.append(_smd_pad(0.8, y - 0.2, 0.4, 0.6, color="#FFAA44"))
        parts.append(_smd_pad(w - 1.2, y - 0.2, 0.4, 0.6, color="#FFAA44"))

    # --- Label ---
    parts.append(_text(1.0, h - 1.0, label, size=1.5, color="#88CCFF"))

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
        ("BU-01_B", 35.0, 39.0, "#006699", "BU-01 Back"),
        ("BU-02_F", 35.0, 39.0, "#CC3300", "BU-02 Front"),
        ("BU-02_B", 35.0, 39.0, "#005533", "BU-02 Back"),
    ]

    for name, w, h, accent, label in configs:
        svg = build_unit_svg(label, w, h, accent)
        path = os.path.join(out_dir, f"{name}.svg")
        with open(path, "w") as f:
            f.write(svg)
        print(f"✅ Created: {path}  ({w}×{h} mm)")

    print(f"\nTest SVGs written to '{out_dir}/'")
    print("Upload them in the app sidebar under '🧪 SVG Layers (Experimental)'")


if __name__ == "__main__":
    main()
