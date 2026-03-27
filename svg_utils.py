"""
svg_utils.py — SVG rendering helpers for the experimental SVG dual-mode backend.

Handles:
  - Parsing uploaded SVG files by naming convention (BU-01_F.svg, BU-02_B.svg …)
  - Extracting the physical viewBox dimensions (mm) from SVG metadata
  - Base64-encoding SVG for use as Plotly layout_image source
  - Rounded-rectangle SVG path generator (ported from faster-aoi shapes.py)

Naming convention:
  BU-{nn}_{side}.svg   where nn = zero-padded buildup number, side = F or B
  Examples: BU-01_F.svg, BU-03_B.svg

The SVG coordinate system is assumed to have its viewBox dimensions in mm.
If the CAM tool exports in a different unit, the caller should pass
`cell_width_mm` and `cell_height_mm` explicitly (manual calibration).
"""

import base64
import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

logger = logging.getLogger(__name__)

# Regex for filename pattern: BU-01_F.svg  or  BU-05_B.svg
_FILENAME_RE = re.compile(r"^BU-(\d{2})_([FB])\.svg$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# SVG store parsing
# ---------------------------------------------------------------------------

def load_svg_store(uploaded_files: list) -> dict[str, str]:
    """Parse uploaded Streamlit UploadedFile objects into a keyed SVG dict.

    Args:
        uploaded_files: List of Streamlit UploadedFile objects with .name and .read().

    Returns:
        Dict mapping ``"BU-{nn}_{F|B}"`` → SVG string (UTF-8 decoded).
        Files that do not match the naming convention are skipped with a warning.

    Example:
        >>> store = load_svg_store(st.session_state['svg_uploads'])
        >>> svg_str = store['BU-01_F']
    """
    store: dict[str, str] = {}
    for f in uploaded_files or []:
        match = _FILENAME_RE.match(f.name)
        if not match:
            logger.warning("SVG file '%s' does not match BU-{nn}_{F|B}.svg — skipped.", f.name)
            continue
        nn, side = match.group(1), match.group(2).upper()
        key = f"BU-{nn}_{side}"
        try:
            content = f.read().decode("utf-8", errors="replace")
            store[key] = content
            logger.info("Loaded SVG: %s (%d bytes)", key, len(content))
        except Exception as exc:
            logger.error("Failed to read SVG '%s': %s", f.name, exc)
    return store


def parse_svg_keys(store: dict[str, str]) -> tuple[list[int], list[str]]:
    """Extract sorted buildup numbers and sides present in the SVG store.

    Args:
        store: Dict from :func:`load_svg_store`.

    Returns:
        ``(buildup_numbers, sides)`` — e.g. ``([1, 2, 3], ['B', 'F'])``
    """
    buildups: set[int] = set()
    sides: set[str] = set()
    for key in store:
        match = re.match(r"BU-(\d+)_([FB])", key)
        if match:
            buildups.add(int(match.group(1)))
            sides.add(match.group(2))
    return sorted(buildups), sorted(sides)


# ---------------------------------------------------------------------------
# viewBox / calibration
# ---------------------------------------------------------------------------

def get_svg_viewbox_mm(svg_string: str) -> Optional[tuple[float, float]]:
    """Extract (width_mm, height_mm) from an SVG ``viewBox`` attribute.

    Assumes the viewBox values are already in mm (as exported by most CAM tools
    when the SVG unit is set to mm). If the attribute is absent or cannot be
    parsed, returns None and the caller should prompt for manual calibration.

    Args:
        svg_string: Raw SVG content as a UTF-8 string.

    Returns:
        ``(width_mm, height_mm)`` or ``None`` if not determinable.
    """
    try:
        root = ET.fromstring(svg_string)
        vb = root.get("viewBox") or root.get("viewbox")
        if vb:
            parts = vb.replace(",", " ").split()
            if len(parts) == 4:
                return float(parts[2]), float(parts[3])  # width, height
        # Fallback: try width/height attributes
        w = root.get("width", "")
        h = root.get("height", "")
        # Strip units (mm, pt, px …)
        w_val = re.sub(r"[^0-9.]", "", w)
        h_val = re.sub(r"[^0-9.]", "", h)
        if w_val and h_val:
            return float(w_val), float(h_val)
    except ET.ParseError as exc:
        logger.warning("Could not parse SVG XML for viewBox extraction: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Plotly encoding
# ---------------------------------------------------------------------------

def svg_to_data_url(svg_string: str) -> str:
    """Base64-encode an SVG string for use as a Plotly layout_image source.

    Args:
        svg_string: Raw SVG content as a UTF-8 string.

    Returns:
        Data URL string: ``data:image/svg+xml;base64,...``
    """
    encoded = base64.b64encode(svg_string.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


# ---------------------------------------------------------------------------
# Panel geometry helpers — ported from faster-aoi shapes.py
# ---------------------------------------------------------------------------

def get_rounded_rect_path(x0: float, y0: float, x1: float, y1: float, r: float) -> str:
    """Generate an SVG/Plotly path string for a rounded rectangle.

    Ported from faster-aoi ``src/plotting/generators/shapes.py``.

    Args:
        x0: Left X coordinate (mm).
        y0: Bottom Y coordinate (mm).
        x1: Right X coordinate (mm).
        y1: Top Y coordinate (mm).
        r: Corner radius (mm). Clamped to fit within dimensions.

    Returns:
        SVG path string compatible with Plotly ``add_shape(type='path', path=...)``.
    """
    width = x1 - x0
    height = y1 - y0
    r = min(r, width / 2.0, height / 2.0)

    return (
        f"M {x0+r} {y0} "
        f"L {x1-r} {y0} "
        f"Q {x1} {y0} {x1} {y0+r} "
        f"L {x1} {y1-r} "
        f"Q {x1} {y1} {x1-r} {y1} "
        f"L {x0+r} {y1} "
        f"Q {x0} {y1} {x0} {y1-r} "
        f"L {x0} {y0+r} "
        f"Q {x0} {y0} {x0+r} {y0} "
        "Z"
    )
