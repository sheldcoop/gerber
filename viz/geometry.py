from __future__ import annotations
"""viz/geometry.py — Shapely→Plotly conversion + PCB layer polygon traces."""

import base64
from io import BytesIO

import numpy as np
import plotly.graph_objects as go
from shapely.geometry import Polygon, MultiPolygon

from core.constants import LAYER_TYPE_COLORS as LAYER_COLORS
from viz.layout import OverlayConfig


def _rgba(color_dict: dict, opacity: float) -> str:
    """Convert an RGB dict + opacity to an rgba() CSS string."""
    return f"rgba({color_dict['r']},{color_dict['g']},{color_dict['b']},{opacity})"


def _polygon_to_coords(polygon: Polygon) -> tuple[list, list]:
    """
    Extract x, y coordinate arrays from a Shapely Polygon, including holes.

    For Plotly fill='toself', holes are created by inserting None values
    between the exterior ring and each interior ring. This creates
    separate closed paths within a single trace.
    """
    xs, ys = [], []

    # Exterior ring
    ex, ey = polygon.exterior.coords.xy
    xs.extend(list(ex))
    ys.extend(list(ey))
    xs.append(None)  # separator
    ys.append(None)

    # Interior rings (holes)
    for interior in polygon.interiors:
        ix, iy = interior.coords.xy
        xs.extend(list(ix))
        ys.extend(list(iy))
        xs.append(None)
        ys.append(None)

    return xs, ys


def _geometry_to_coords(geom) -> tuple[list, list]:
    """
    Convert any Shapely geometry to Plotly-compatible x, y arrays.

    Handles Polygon, MultiPolygon, and GeometryCollection.
    All sub-geometries are concatenated with None separators so they
    render as a single Plotly trace with multiple filled regions.
    """
    xs, ys = [], []

    if isinstance(geom, Polygon):
        if not geom.is_empty:
            px, py = _polygon_to_coords(geom)
            xs.extend(px)
            ys.extend(py)

    elif isinstance(geom, MultiPolygon):
        for poly in geom.geoms:
            if not poly.is_empty:
                px, py = _polygon_to_coords(poly)
                xs.extend(px)
                ys.extend(py)

    else:
        # GeometryCollection — extract polygon-like geometries
        try:
            for sub_geom in geom.geoms:
                if isinstance(sub_geom, (Polygon, MultiPolygon)):
                    sx, sy = _geometry_to_coords(sub_geom)
                    xs.extend(sx)
                    ys.extend(sy)
        except (AttributeError, TypeError):
            pass

    return xs, ys


# ---------------------------------------------------------------------------
# PCB layer traces
# ---------------------------------------------------------------------------

# Render priority: lower index = drawn first (furthest back in z-order)
# profile first so the board outline is always the bottom-most trace,
# then copper fills, then soldermask on top of copper, paste last.
_RENDER_PRIORITY = {
    'outline':    0,
    'copper':     1,
    'soldermask': 2,
    'paste':      3,
    'drill':      4,
    'silkscreen': 5,
    'other':      6,
}


_RASTER_THRESHOLD = 8_000  # polygon count above which we rasterize to PNG


def _rasterize_layer(layer, bounds: tuple, color_dict: dict,
                      opacity: float, px: int = 2048) -> str | None:
    """
    Render a dense layer (>_RASTER_THRESHOLD polygons) to a transparent PNG
    and return a base64 data URL.  PIL/Pillow is used for speed.

    Returns None if Pillow is unavailable or rendering fails.
    """
    minx, miny, maxx, maxy = bounds
    bw, bh = maxx - minx, maxy - miny
    if bw <= 0 or bh <= 0:
        return None

    r_v, g_v, b_v = color_dict['r'] / 255.0, color_dict['g'] / 255.0, color_dict['b'] / 255.0

    # Collect polygon vertices for matplotlib PolyCollection (fast C-level batch render)
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.collections import PolyCollection

        aspect = bh / bw
        fig_w = px / 100.0
        fig_h = max(1.0, fig_w * aspect)
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=100)
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
        ax.set_aspect('equal')
        ax.axis('off')
        fig.patch.set_alpha(0.0)
        ax.patch.set_alpha(0.0)
        plt.subplots_adjust(left=0, right=1, top=1, bottom=0)

        verts = []
        for poly in layer.polygons:
            try:
                if hasattr(poly, 'exterior'):
                    verts.append(np.array(poly.exterior.coords))
            except Exception:
                continue

        if verts:
            coll = PolyCollection(
                verts,
                facecolor=(r_v, g_v, b_v, opacity),
                edgecolor='none',
            )
            ax.add_collection(coll)

        buf = BytesIO()
        fig.savefig(buf, format='PNG', dpi=100, transparent=True,
                    bbox_inches=None, pad_inches=0)
        plt.close(fig)
        buf.seek(0)
        return "data:image/png;base64," + base64.b64encode(buf.read()).decode()

    except Exception:
        pass

    # Fallback: PIL per-polygon (slower but no matplotlib dependency)
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError:
        return None

    aspect = bh / bw
    py_px = max(1, int(px * aspect))
    img = Image.new('RGBA', (px, py_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = (int(r_v * 255), int(g_v * 255), int(b_v * 255), int(opacity * 255))

    def to_px(x: float, y: float) -> tuple:
        xi = int((x - minx) / bw * (px - 1))
        yi = int((maxy - y) / bh * (py_px - 1))
        return xi, yi

    for poly in layer.polygons:
        try:
            coords = list(poly.exterior.coords)
            if len(coords) < 3:
                continue
            pts = [to_px(cx, cy) for cx, cy in coords]
            draw.polygon(pts, fill=fill)
        except Exception:
            continue

    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return "data:image/png;base64," + base64.b64encode(buf.read()).decode()


def _add_layer_traces(
    fig: go.Figure,
    layers: dict,
    config: OverlayConfig,
) -> None:
    """
    Add PCB layer polygon traces to the Plotly figure.

    Layers are sorted by _RENDER_PRIORITY so copper is always drawn before
    soldermask (which sits on top at reduced opacity).  The board outline
    (profile) is rendered as a stroke-only trace with no fill, keeping it
    visible as a crisp border regardless of what's layered above it.

    Each visible layer is a SINGLE Plotly trace with None-separated coordinate
    arrays — critical for performance with thousands of shapes.
    """
    ordered = sorted(
        [n for n in config.visible_layers if n in layers],
        key=lambda n: _RENDER_PRIORITY.get(layers[n].layer_type, 6),
    )

    copper_colors = [
        {'r': 184, 'g': 115, 'b': 51},   # Copper
        {'r': 31,  'g': 119, 'b': 180},  # Deep Blue
        {'r': 44,  'g': 160, 'b': 44},   # Green
        {'r': 148, 'g': 103, 'b': 189}   # Purple
    ]
    copper_idx = 0

    trace_count = 0
    # Pre-calculate active viewport bounds translated mathematically back to source origin
    cx1, cy1, cx2, cy2 = (None,) * 4
    if config.crop_bounds:
        cx1 = config.crop_bounds[0] - config.offset_x
        cy1 = config.crop_bounds[1] - config.offset_y
        cx2 = config.crop_bounds[2] - config.offset_x
        cy2 = config.crop_bounds[3] - config.offset_y

    for layer_name in ordered:
        if layer_name not in layers:
            continue
            
        layer = layers[layer_name]
        trace_count += layer.polygon_count

        opacity = config.layer_opacities.get(layer_name, 0.6)

        color_dict = LAYER_COLORS.get(layer.layer_type, LAYER_COLORS['other'])
        if layer.layer_type == 'copper':
            color_dict = copper_colors[copper_idx % len(copper_colors)]
            copper_idx += 1

        line_color = _rgba(color_dict, min(1.0, opacity + 0.3))

        # ── RASTERIZATION PATH ─────────────────────────────────────────────
        # Dense layers (>8k polygons) are rasterized to PNG for instant
        # browser rendering. Sending 50k+ SVG paths freezes any browser.
        poly_count = len(layer.polygons)
        _is_outline = (layer.layer_type == 'outline')
        if poly_count > _RASTER_THRESHOLD and not _is_outline:
            render_bounds = config.board_bounds
            if render_bounds and render_bounds != (0, 0, 0, 0):
                rbnds = render_bounds
            else:
                rbnds = layer.bounds if layer.bounds else (0, 0, 1, 1)
            # Shift bounds by offset
            ox, oy = config.offset_x, config.offset_y
            rb = (rbnds[0] + ox, rbnds[1] + oy, rbnds[2] + ox, rbnds[3] + oy)
            data_url = _rasterize_layer(layer, rb, color_dict, opacity)
            if data_url:
                fig.add_layout_image(dict(
                    source=data_url,
                    xref='x', yref='y',
                    x=rb[0], y=rb[3],          # top-left in Plotly coords
                    sizex=rb[2] - rb[0],
                    sizey=rb[3] - rb[1],
                    sizing='stretch',
                    layer='below',
                    opacity=1.0,               # opacity already baked into PNG
                    name=layer_name,
                ))
                # Add a dummy invisible trace so the layer appears in legend
                fig.add_trace(go.Scatter(
                    x=[None], y=[None],
                    mode='markers',
                    marker=dict(color=_rgba(color_dict, opacity), size=10, symbol='square'),
                    name=f"{layer_name} ({poly_count:,} features — rasterized)",
                    legendgroup=layer_name,
                    showlegend=True,
                ))
                continue  # skip vector path below

        # ── VECTOR PATH (< 8k polygons) ────────────────────────────────────
        is_outline = _is_outline
        # Merge all polygons into coordinate arrays
        all_x, all_y = [], []
        has_widths = hasattr(layer, 'trace_widths') and len(layer.trace_widths) == len(layer.polygons)
        min_size = config.min_feature_size
        for poly_idx, poly in enumerate(layer.polygons):
            # --- LOD FILTERING: skip sub-threshold features at full-panel zoom ---
            if min_size is not None and has_widths:
                if layer.trace_widths[poly_idx] < min_size:
                    continue
            # --- HIGH PERFORMANCE AABB CULLING ENGINE ---
            if cx1 is not None and cx2 is not None:
                minx, miny, maxx, maxy = poly.bounds
                # Frustum Collision Check: if trace sits entirely outside bounding box edges, implicitly bin it
                if minx > cx2 or maxx < cx1 or miny > cy2 or maxy < cy1:
                    continue
                    
            px, py = _geometry_to_coords(poly)
            # Apply visual offset directly so axes reflect physical panel topology
            if config.offset_x != 0.0 or config.offset_y != 0.0:
                px = [x + config.offset_x if x is not None else None for x in px]
                py = [y + config.offset_y if y is not None else None for y in py]
            all_x.extend(px)
            all_y.extend(py)

        if not all_x:
            continue

        is_outline = (layer.layer_type == 'outline')

        if is_outline:
            # Board outline: stroke only, no fill — board shape stays visible
            # regardless of what is layered above it
            fig.add_trace(go.Scatter(
                x=all_x,
                y=all_y,
                mode='lines',
                line=dict(color='white', width=2.0),
                name=f"{layer_name} ({layer.polygon_count} shapes)",
                legendgroup=layer_name,
                showlegend=True,
                hoverinfo='name',
                hoverlabel=dict(namelength=-1),
            ))
        else:
            fill_color = _rgba(color_dict, opacity)
            fig.add_trace(go.Scatter(
                x=all_x,
                y=all_y,
                fill='toself',
                fillcolor=fill_color,
                line=dict(color=line_color, width=0.5),
                name=f"{layer_name} ({layer.polygon_count} shapes)",
                legendgroup=layer_name,
                showlegend=True,
                hoverinfo='name',
                hoverlabel=dict(namelength=-1),
            ))


# ---------------------------------------------------------------------------
# Defect traces
# ---------------------------------------------------------------------------
