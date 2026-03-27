from __future__ import annotations
"""
visualizer.py — Plotly figure builder for PCB layer + AOI defect overlay.

Converts Shapely polygons from the ODB++ parser into Plotly scatter traces
with fill='toself', and overlays AOI defect markers with configurable styles.

Key design decisions:
- Each PCB layer is rendered as a SINGLE trace with None-separated coordinate
  arrays. This is critical for Plotly performance — 50,000 individual traces
  would be unusable, but one trace with None-separated sub-paths is fast.
- Defects are grouped by color mode (type/buildup/severity) for the legend.
- Aspect ratio is locked to real board dimensions via scaleanchor='y'.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from shapely.geometry import Polygon, MultiPolygon


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class OverlayConfig:
    """Configuration for the overlay visualization."""
    visible_layers: list[str] = field(default_factory=list)
    layer_opacities: dict[str, float] = field(default_factory=dict)
    defect_types: list[str] = field(default_factory=list)
    buildup_filter: list[int] = field(default_factory=list)
    side_filter: str = 'Both'        # 'Front', 'Back', 'Both'
    marker_style: str = 'dot'        # 'dot', 'crosshair', 'x_mark'
    color_mode: str = 'by_type'      # 'by_type', 'by_buildup', 'by_severity'
    board_bounds: tuple[float, float, float, float] = (0, 0, 0, 0)
    offset_x: float = 0.0            # Visual X translation for the ODB++ render
    offset_y: float = 0.0            # Visual Y translation for the ODB++ render
    active_defect_x: float | None = None  # X coordinate for VRS targeting
    active_defect_y: float | None = None  # Y coordinate for VRS targeting
    crop_bounds: tuple[float, float, float, float] | None = None # Explicit viewport bounds for Geometry culling
    min_feature_size: float | None = None  # LOD: skip features narrower than this (mm)


# Engineering-standard colors for PCB layers
LAYER_COLORS = {
    'copper':      {'r': 184, 'g': 115, 'b': 51},   # copper brown
    'soldermask':  {'r': 0,   'g': 128, 'b': 0},     # PCB green
    'silkscreen':  {'r': 255, 'g': 255, 'b': 255},   # white
    'paste':       {'r': 192, 'g': 192, 'b': 192},   # silver
    'outline':     {'r': 255, 'g': 215, 'b': 0},     # gold
    'drill':       {'r': 100, 'g': 100, 'b': 100},   # dark grey
    'other':       {'r': 100, 'g': 100, 'b': 200},   # muted blue
}

# Distinct colors for defect types (categorical palette)
DEFECT_TYPE_COLORS = [
    '#FF4444', '#44FF44', '#4444FF', '#FFAA00', '#FF44FF',
    '#44FFFF', '#FF8844', '#88FF44', '#4488FF', '#FF4488',
    '#AAFF44', '#44AAFF', '#FF44AA', '#44FFAA', '#AA44FF',
    '#FFFF44', '#FF6644', '#66FF44', '#4466FF', '#FF4466',
]

# Buildup layer colors (sequential blue-to-red)
BUILDUP_COLORS = [
    '#2196F3', '#4CAF50', '#FF9800', '#F44336', '#9C27B0',
    '#00BCD4', '#FFEB3B', '#795548', '#607D8B', '#E91E63',
]

# Marker style configurations
MARKER_STYLES = {
    'dot': {
        'symbol': 'circle',
        'size': 8,
        'line': {'width': 1, 'color': 'black'},
    },
    'crosshair': {
        'symbol': 'cross',
        'size': 12,
        'line': {'width': 2, 'color': 'black'},
    },
    'x_mark': {
        'symbol': 'x',
        'size': 10,
        'line': {'width': 2, 'color': 'black'},
    },
}


# ---------------------------------------------------------------------------
# Shapely → Plotly conversion
# ---------------------------------------------------------------------------

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

def _build_hover_template(df: pd.DataFrame) -> str:
    """Build a rich hover template showing all available defect metadata."""
    parts = [
        "<b>%{customdata[0]}</b>",  # DEFECT_TYPE
        "X: %{x:.3f} mm",
        "Y: %{y:.3f} mm",
    ]

    # Add optional fields based on what columns exist
    idx = 1
    for col, label in [
        ('DEFECT_ID', 'ID'),
        ('BUILDUP', 'Buildup'),
        ('SIDE', 'Side'),
        ('VERIFICATION', 'Verification'),
        ('UNIT_INDEX_X', 'Unit X'),
        ('UNIT_INDEX_Y', 'Unit Y'),
        ('SOURCE_FILE', 'Source'),
    ]:
        if col in df.columns:
            parts.append(f"{label}: %{{customdata[{idx}]}}")
            idx += 1

    parts.append("<extra></extra>")
    return "<br>".join(parts)


def _build_customdata(df: pd.DataFrame) -> np.ndarray:
    """Build the customdata array for hover tooltips."""
    cols = ['DEFECT_TYPE']
    for col in ['DEFECT_ID', 'BUILDUP', 'SIDE', 'VERIFICATION',
                'UNIT_INDEX_X', 'UNIT_INDEX_Y', 'SOURCE_FILE']:
        if col in df.columns:
            cols.append(col)
    return df[cols].values


def _add_defect_traces(
    fig: go.Figure,
    df: pd.DataFrame,
    config: OverlayConfig,
) -> None:
    """
    Add AOI defect scatter markers to the Plotly figure.

    Defects are filtered by the config settings (defect type, buildup, side)
    and grouped by the selected color mode for the legend.
    """
    if df.empty or 'ALIGNED_X' not in df.columns:
        return

    # Apply filters
    mask = pd.Series(True, index=df.index)

    if config.defect_types:
        mask &= df['DEFECT_TYPE'].isin(config.defect_types)

    if config.buildup_filter and 'BUILDUP' in df.columns:
        mask &= df['BUILDUP'].isin(config.buildup_filter)

    if config.side_filter != 'Both' and 'SIDE' in df.columns:
        side_code = 'F' if config.side_filter == 'Front' else 'B'
        mask &= df['SIDE'] == side_code

    filtered = df[mask].copy()
    if filtered.empty:
        return

    # Highlight active defect (VRS Mode)
    if config.active_defect_x is not None and config.active_defect_y is not None:
        fig.add_trace(go.Scatter(
            x=[config.active_defect_x],
            y=[config.active_defect_y],
            mode='markers',
            marker=dict(
                size=40,
                color='rgba(0,0,0,0)',
                line=dict(color='#00FFCC', width=4)
            ),
            name="VRS Active Target",
            hoverinfo='skip',
            showlegend=False
        ))

    # Determine grouping column and color palette
    if config.color_mode == 'by_buildup' and 'BUILDUP' in filtered.columns:
        group_col = 'BUILDUP'
        palette = BUILDUP_COLORS
    elif config.color_mode == 'by_severity' and 'DEFECT_TYPE' in filtered.columns:
        # Map defect types to severity levels (heuristic based on common AOI types)
        severity_map = _build_severity_map(filtered['DEFECT_TYPE'].unique())
        filtered['_SEVERITY'] = filtered['DEFECT_TYPE'].map(severity_map)
        group_col = '_SEVERITY'
        palette = ['#4CAF50', '#FFEB3B', '#FF9800', '#F44336']  # green→yellow→orange→red
    else:
        group_col = 'DEFECT_TYPE'
        palette = DEFECT_TYPE_COLORS

    # Get marker style
    marker_config = MARKER_STYLES.get(config.marker_style, MARKER_STYLES['dot'])

    # Build hover template
    hover_template = _build_hover_template(filtered)
    customdata = _build_customdata(filtered)

    # Add one trace per group
    groups = filtered.groupby(group_col, observed=True)
    for i, (group_name, group_df) in enumerate(groups):
        color = palette[i % len(palette)]

        # Build customdata for this group
        group_customdata = _build_customdata(group_df)

        fig.add_trace(go.Scatter(
            x=group_df['ALIGNED_X'],
            y=group_df['ALIGNED_Y'],
            mode='markers',
            marker=dict(
                color=color,
                symbol=marker_config['symbol'],
                size=marker_config['size'],
                line=marker_config['line'],
            ),
            name=f"Defect: {group_name} ({len(group_df)})",
            legendgroup=f"defect_{group_name}",
            showlegend=True,
            customdata=group_customdata,
            hovertemplate=hover_template,
        ))


def _build_severity_map(defect_types) -> dict:
    """
    Map defect types to severity levels (0-3) based on common AOI heuristics.

    Severity levels:
      0 = Low (cosmetic): Minimum Line, Protrusion
      1 = Medium (minor): Nick, Deformation
      2 = High (functional): Space, Island, Cut
      3 = Critical (fatal): Short, Open, Missing
    """
    severity_keywords = {
        3: ['short', 'open', 'missing', 'bridge', 'break'],
        2: ['space', 'island', 'cut', 'excess', 'pinhole', 'void'],
        1: ['nick', 'deformation', 'scratch', 'dent', 'mark'],
        0: ['minimum', 'protrusion', 'roughness', 'residue', 'discolor'],
    }

    result = {}
    for dtype in defect_types:
        dtype_lower = str(dtype).lower()
        assigned = 1  # default: medium
        for severity, keywords in severity_keywords.items():
            if any(kw in dtype_lower for kw in keywords):
                assigned = severity
                break
        severity_labels = ['Low', 'Medium', 'High', 'Critical']
        result[dtype] = severity_labels[assigned]

    return result


# ---------------------------------------------------------------------------
# Layout configuration
# ---------------------------------------------------------------------------

def _apply_layout(fig: go.Figure, config: OverlayConfig) -> None:
    """
    Apply Plotly layout settings: dark theme, locked aspect ratio,
    zoom/pan controls, and board-sized axis ranges.
    """
    bounds = config.board_bounds
    margin_pct = 0.05  # 5% margin around board

    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    margin_x = width * margin_pct if width > 0 else 1
    margin_y = height * margin_pct if height > 0 else 1

    fig.update_layout(
        # Neutral dark theme — no blue tint
        plot_bgcolor='#111111',
        paper_bgcolor='#1a1a1a',
        font=dict(color='#cccccc', size=12),

        # Axis configuration with locked aspect ratio
        xaxis=dict(
            title='X (mm)',
            range=[bounds[0] - margin_x, bounds[2] + margin_x],
            showgrid=True,
            gridcolor='rgba(255,255,255,0.06)',
            gridwidth=1,
            zeroline=False,
            zerolinecolor='rgba(255,255,255,0.15)',
            tickcolor='#555555',
            linecolor='#333333',
            dtick=_smart_tick(width),
        ),
        yaxis=dict(
            title='Y (mm)',
            range=[bounds[1] - margin_y, bounds[3] + margin_y],
            scaleanchor='x',
            scaleratio=1,
            showgrid=True,
            gridcolor='rgba(255,255,255,0.06)',
            gridwidth=1,
            zeroline=False,
            zerolinecolor='rgba(255,255,255,0.15)',
            tickcolor='#555555',
            linecolor='#333333',
            dtick=_smart_tick(height),
        ),

        # Legend
        legend=dict(
            bgcolor='rgba(20,20,20,0.92)',
            bordercolor='rgba(255,255,255,0.18)',
            borderwidth=1,
            font=dict(size=11),
            itemclick='toggle',
            itemdoubleclick='toggleothers',
        ),

        # Interaction
        dragmode='pan',
        hovermode='closest',
        margin=dict(l=60, r=20, t=40, b=60),

        # Size
        height=800,
    )


def _smart_tick(axis_range: float) -> Optional[float]:
    """Compute a sensible tick interval based on axis range."""
    if axis_range <= 0:
        return None
    # Target ~10-20 ticks
    raw = axis_range / 15
    # Round to nearest power of 10, 2, or 5
    magnitude = 10 ** int(np.floor(np.log10(raw)))
    for multiplier in [1, 2, 5, 10]:
        if magnitude * multiplier >= raw:
            return magnitude * multiplier
    return magnitude * 10


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_overlay_figure(
    gerber_layers: dict,
    defect_df: pd.DataFrame,
    config: OverlayConfig,
    drill_hits: list | None = None,
    components: list | None = None,
) -> go.Figure:
    """
    Build the complete Plotly overlay figure with Gerber layers and AOI defects.

    Args:
        gerber_layers: dict of layer_name → GerberLayer from the parser
        defect_df: AOI defect DataFrame with ALIGNED_X, ALIGNED_Y columns
        config: OverlayConfig controlling visibility, filters, and styling
        drill_hits: Optional list of DrillHit objects from the ODB++ parser
        components: Optional list of ComponentPlacement objects from the ODB++ parser

    Returns:
        go.Figure ready for st.plotly_chart()
    """
    fig = go.Figure()

    # 1. Add PCB layer polygons (bottom of z-order)
    if gerber_layers:
        _add_layer_traces(fig, gerber_layers, config)

    # 2. Add drill hits (rendered as hollow circles — dark drill through copper)
    if drill_hits:
        _add_drill_hit_traces(fig, drill_hits, config)

    # 3. Add component outlines and refdes
    if components:
        _add_component_traces(fig, components, config)

    # 4. Add AOI defect markers (top of z-order)
    if defect_df is not None and not defect_df.empty:
        _add_defect_traces(fig, defect_df, config)

    # 5. Apply layout
    _apply_layout(fig, config)

    return fig


def _add_drill_hit_traces(fig: go.Figure, drill_hits: list, config: OverlayConfig) -> None:
    """Render drill holes as dark-filled circle markers on top of copper."""
    if not drill_hits:
        return

    ox, oy = config.offset_x, config.offset_y
    xs, ys, sizes, texts = [], [], [], []

    for hit in drill_hits:
        x, y = hit.x + ox, hit.y + oy
        # Apply viewport culling
        if config.crop_bounds:
            cb = config.crop_bounds
            if x < cb[0] or x > cb[2] or y < cb[1] or y > cb[3]:
                continue
        # Convert mm diameter to approx pixel size (Plotly marker size is in px)
        # 1mm ≈ 3.78px at 96dpi, but we work in mm-space so use a visual scale
        px_size = max(4, min(20, int(hit.diameter * 4)))
        xs.append(x)
        ys.append(y)
        sizes.append(px_size)
        texts.append(f"Drill: ⌀{hit.diameter:.3f}mm ({hit.layer_name})")

    if not xs:
        return

    fig.add_trace(go.Scatter(
        x=xs, y=ys,
        mode='markers',
        marker=dict(
            symbol='circle',
            size=sizes,
            color='#111111',
            line=dict(color='#555555', width=1),
        ),
        name='Drill holes',
        hovertext=texts,
        hoverinfo='text',
        showlegend=True,
    ))


def _add_component_traces(fig: go.Figure, components: list, config: OverlayConfig) -> None:
    """Render component centroids and reference designators."""
    if not components:
        return

    ox, oy = config.offset_x, config.offset_y
    top_x, top_y, top_text = [], [], []
    bot_x, bot_y, bot_text = [], [], []

    for comp in components:
        x, y = comp.x + ox, comp.y + oy
        if config.crop_bounds:
            cb = config.crop_bounds
            if x < cb[0] or x > cb[2] or y < cb[1] or y > cb[3]:
                continue
        label = f"{comp.refdes}<br>{comp.part_type}<br>({comp.side}) rot={comp.rotation:.0f}°"
        if comp.side == 'T':
            top_x.append(x); top_y.append(y); top_text.append(label)
        else:
            bot_x.append(x); bot_y.append(y); bot_text.append(label)

    if top_x:
        fig.add_trace(go.Scatter(
            x=top_x, y=top_y, mode='markers+text',
            marker=dict(symbol='square', size=6, color='rgba(255,200,0,0.7)',
                        line=dict(color='#FFCC00', width=1)),
            text=[t.split('<br>')[0] for t in top_text],  # refdes only as label
            textposition='top center',
            textfont=dict(size=7, color='#FFCC00'),
            hovertext=top_text, hoverinfo='text',
            name='Components (Top)', showlegend=True,
        ))

    if bot_x:
        fig.add_trace(go.Scatter(
            x=bot_x, y=bot_y, mode='markers+text',
            marker=dict(symbol='square', size=6, color='rgba(0,200,255,0.7)',
                        line=dict(color='#00CCFF', width=1)),
            text=[t.split('<br>')[0] for t in bot_text],
            textposition='top center',
            textfont=dict(size=7, color='#00CCFF'),
            hovertext=bot_text, hoverinfo='text',
            name='Components (Bot)', showlegend=True,
        ))


def build_defect_only_figure(
    defect_df: pd.DataFrame,
    config: OverlayConfig,
) -> go.Figure:
    """
    Build a Plotly figure with only AOI defects (no Gerber layers).

    Useful when no Gerber archive is uploaded but AOI data is available.
    """
    fig = go.Figure()

    if defect_df is not None and not defect_df.empty:
        _add_defect_traces(fig, defect_df, config)

    _apply_layout(fig, config)
    return fig
