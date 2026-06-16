import re
import base64

svg = """<svg xmlns="http://www.w3.org/2000/svg" width="43.5" height="37.5" viewBox="0 0 43.5 37.5">
<rect x="0" y="0" width="43.5" height="37.5" fill="red"/>
</svg>"""

_re_svg = re.compile(r'<svg[^>]+>', re.IGNORECASE)
_re_viewbox = re.compile(r'viewBox=[\"\'][^\"\']+[\"\']')
_re_viewbox_capture = re.compile(r'viewBox=[\"\']([^\'\"]+)[\"\']')
_re_width = re.compile(r'\swidth=[\"\'][^\"\']+[\"\']')
_re_height = re.compile(r'\sheight=[\"\'][^\"\']+[\"\']')

def build_rotated(svg, rot_deg):
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
                    new_vx = cx - vh / 2
                    new_vy = cy - vw / 2
                    new_vb = f"{new_vx:.4f} {new_vy:.4f} {vh:.4f} {vw:.4f}"
                
                svg_start = svg[:tag.end()]
                svg_start = _re_viewbox.sub(f'viewBox="{new_vb}"', svg_start)
                
                # Strip width and height from the <svg> tag to allow fluid scaling
                svg_start = _re_width.sub('', svg_start)
                svg_start = _re_height.sub('', svg_start)
                
                svg = (
                    svg_start
                    + f'<g transform="rotate({rot_deg},{cx:.4f},{cy:.4f})">'
                    + inner + '</g>' + svg[close:]
                )
    return svg

print(build_rotated(svg, 90))
