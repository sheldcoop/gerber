"""
core/panel_builder.py — Composite unit SVG into a panel SVG using <use> tiling.
"""

from core.cache import _svg_to_data_url_fast


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
