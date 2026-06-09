import re
import base64
from typing import Any

# Opaque panel-background colour baked into every layer SVG by the renderer.
_SVG_BG = '#060A06'

# Shared palette for distinguishing layers when several are shown together.
# Lives here (a leaf module) so both the views and the sidebar import the same
# colours without risking a circular import.
LAYER_PALETTE = [
    '#FF9800',  # amber
    '#2196F3',  # blue
    '#4CAF50',  # green
    '#9C27B0',  # purple
    '#00BCD4',  # cyan
    '#E91E63',  # pink
    '#CDDC39',  # lime
    '#F44336',  # red
]


def stable_layer_colors(layer_names) -> dict:
    """Map each layer name to a fixed palette colour by sorted identity.

    Keying off the sorted layer name (rather than its position in the *checked*
    list) means a layer keeps the same hue no matter which other layers are
    selected — so the sidebar swatch always matches what's on screen.
    """
    ordered = sorted(layer_names)
    return {n: LAYER_PALETTE[i % len(LAYER_PALETTE)] for i, n in enumerate(ordered)}

# Regex to match the SVG tag and the viewBox attribute
_re_svg = re.compile(r'<svg[^>]+>', re.IGNORECASE)
_re_viewbox = re.compile(r'viewBox=[\"\'][^\"\']+[\"\']')
_re_viewbox_capture = re.compile(r'viewBox=[\"\']([^\'\"]+)[\"\']')
_re_width = re.compile(r'\s+width=[\"\'][^\"\']+[\"\']', re.IGNORECASE)
_re_height = re.compile(r'\s+height=[\"\'][^\"\']+[\"\']', re.IGNORECASE)


def build_stack_svg(svg_string: str, fg_color: str, stack_color: str) -> str:
    """Build the layer SVG variant used when stacking multiple layers.

    Recolours the foreground to ``stack_color`` and makes the background
    transparent (``fill="none"``) so layers beneath show through. The background
    must be stripped even when ``fg_color == stack_color`` (the first copper
    layer, whose stack colour already equals the copper foreground).
    """
    svg = svg_string
    if fg_color != stack_color:
        svg = svg.replace(fg_color, stack_color)
    return svg.replace(_SVG_BG, 'none')


def _inject_outline(svg: str, color: str) -> str:
    """Turn every filled shape in an isolated layer SVG into a thin stroked outline.

    Used by the 'outline' render mode so that several dense copper pours can be
    stacked and each remains visible (the solid fills would otherwise occlude
    one another). Scoped to this single SVG document, so the aggressive ``*``
    selector is safe.
    """
    tag = _re_svg.search(svg)
    if not tag:
        return svg
    style = (f'<style>*{{fill:none !important;stroke:{color} !important;'
             f'stroke-width:0.06px !important;}}</style>')
    return svg[:tag.end()] + style + svg[tag.end():]


def build_rotated_svg_url(lyr_obj: Any, rot_deg: float, is_multi: bool = False,
                          invert: bool = False, layer_color: str = None,
                          outline: bool = False) -> str:
    """
    Builds a data URL for an SVG layer, optionally rotating the SVG contents
    and injecting colours based on layer type.

    Args:
        lyr_obj:     Layer object containing svg_string or color_svg_urls.
        rot_deg:     Background rotation in degrees.
        is_multi:    True if rendering multiple layers simultaneously.
        invert:      Swap foreground/background colours (invert polarity).
        layer_color: Optional hex color (e.g. '#2196F3') to substitute the copper
                     foreground with — used to give each layer a distinct hue when
                     multiple layers are shown together.
        outline:     Render shapes as thin outlines instead of solid fills, so
                     stacked dense layers don't occlude one another.

    Returns:
        A base64 encoded data URI string for the SVG.
    """
    # Outline mode always needs the full svg_string path so it can restyle fills.
    # Precomputed URLs only apply when neither rotating, inverting, nor recolouring.
    if not invert and not layer_color and not outline:
        if is_multi and getattr(lyr_obj, 'color_svg_urls', None):
            if rot_deg == 0:
                return next(iter(lyr_obj.color_svg_urls.values()))
        elif rot_deg == 0:
            # Single-layer view: use a TRANSPARENT-background variant (natural colour,
            # bg stripped to 'none') so Plotly's image opacity reveals the defects/grid
            # and any layers beneath instead of merely dimming an opaque black tile.
            # Cached lazily on the layer instance so we pay the recolour cost once.
            cached = getattr(lyr_obj, '_transparent_data_url', None)
            if cached:
                return cached
            svg_src = getattr(lyr_obj, 'svg_string', '')
            if svg_src:
                t_svg = svg_src.replace(_SVG_BG, 'none')
                url = ('data:image/svg+xml;base64,'
                       + base64.b64encode(t_svg.encode()).decode())
                try:
                    setattr(lyr_obj, '_transparent_data_url', url)
                except Exception:
                    pass
                return url
            if getattr(lyr_obj, 'svg_data_url', None):
                return lyr_obj.svg_data_url

    # ── Fast path for per-layer colour override (no rotation, no invert) ──────
    # The precomputed color_svg_urls value is a *verified* transparent-background
    # SVG produced by build_stack_svg().  Using it as the base avoids the fragile
    # svg_string path where the background colour string may differ from _SVG_BG
    # (e.g. if the renderer used CSS rather than an attribute value).  We just
    # swap the precomputed stack colour with the requested layer_color.
    if layer_color and not invert and not outline and abs(rot_deg) < 0.01:
        stack_urls = getattr(lyr_obj, 'color_svg_urls', {})
        if stack_urls:
            stack_color = next(iter(stack_urls.keys()))
            stack_url   = stack_urls[stack_color]
            try:
                _b64_data = stack_url.split(',', 1)[1]
                stack_svg = base64.b64decode(_b64_data).decode('utf-8')
                if layer_color != stack_color:
                    stack_svg = stack_svg.replace(stack_color, layer_color)
                return ('data:image/svg+xml;base64,'
                        + base64.b64encode(stack_svg.encode()).decode())
            except Exception:
                pass  # fall through to the svg_string path below

    svg = getattr(lyr_obj, 'svg_string', "")
    _natural = '#FFD700' if getattr(lyr_obj, 'layer_type', '') == 'drill' else '#b87333'

    # These render modes are mutually exclusive — applying more than one corrupts the
    # colours. Outline (contour) wins for copper and stays a contour even when inverted,
    # so a copper pour never mashes into a solid blob.
    if outline:
        color = layer_color or _natural
        if invert:
            # Inverted contour: dark wireframe on a colored field (opaque) — never mashes.
            # The bg replace runs first; the dark stroke colour injected afterwards is a
            # fresh literal, so it isn't retro-replaced by the field colour.
            svg = svg.replace(_SVG_BG, color)
            svg = _inject_outline(svg, _SVG_BG)
        else:
            # Normal contour: coloured wireframe, transparent bg so layers stack.
            svg = svg.replace(_SVG_BG, 'none')
            svg = _inject_outline(svg, color)
    elif invert:
        # Solid negative for non-outline layers (soldermask/drill): the field takes the
        # (layer) colour and the features go dark. These layers are sparse, so no mash.
        field = layer_color or _natural
        _t = '__PS__'
        svg = (svg.replace(_natural, _t)
                  .replace(_SVG_BG, field)
                  .replace(_t, _SVG_BG))
    elif layer_color:
        # Normal colour with transparent bg so layers stack and opacity reveals beneath.
        svg = svg.replace(_natural, layer_color).replace(_SVG_BG, 'none')

    if abs(rot_deg) < 0.01:
        return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()

    # Perform SVG Rotation
    vb = _re_viewbox_capture.search(svg)
    if vb:
        vx, vy, vw, vh = map(float, vb.group(1).split())
        cx, cy = vx + vw / 2, vy + vh / 2
        tag = _re_svg.search(svg)
        if tag:
            close = svg.rfind('</svg>')
            if close >= 0:
                inner = svg[tag.end():close]
                rot_norm = round(rot_deg) % 360
                new_vb = f"{vx} {vy} {vw} {vh}"
                if rot_norm in (90, 270):
                    # Swap width and height, keep cx/cy same
                    new_vx = cx - vh / 2
                    new_vy = cy - vw / 2
                    new_vb = f"{new_vx:.4f} {new_vy:.4f} {vh:.4f} {vw:.4f}"
                
                svg_start = svg[:tag.end()]
                svg_start = _re_viewbox.sub(f'viewBox="{new_vb}"', svg_start)
                
                # Strip hardcoded width/height so it scales fluidly by viewBox
                svg_start = _re_width.sub('', svg_start)
                svg_start = _re_height.sub('', svg_start)
                
                svg = (
                    svg_start
                    + f'<g transform="rotate({rot_deg},{cx:.4f},{cy:.4f})">'
                    + inner + '</g>' + svg[close:]
                )

    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()
