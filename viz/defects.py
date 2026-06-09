from __future__ import annotations
"""viz/defects.py — AOI defect markers, hover/customdata, drill + component traces."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from core.constants import DEFECT_TYPE_COLORS, MARKER_STYLES
from viz.layout import OverlayConfig


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
        fig.add_trace(go.Scattergl(
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

    # Determine grouping column and color palette. Only two modes are supported:
    # by_verification (when the column exists) and the default by_type.
    if config.color_mode == 'by_verification' and 'VERIFICATION' in filtered.columns:
        group_col = 'VERIFICATION'
        palette = DEFECT_TYPE_COLORS
    else:
        group_col = 'DEFECT_TYPE'
        palette = DEFECT_TYPE_COLORS

    # Get marker style
    marker_config = MARKER_STYLES.get(config.marker_style, MARKER_STYLES['dot'])

    # Build hover template
    hover_template = _build_hover_template(filtered)
    customdata = _build_customdata(filtered)

    # Add one trace per group — color is stable (hash of name), not position-dependent.
    # This ensures CU22 is always the same color regardless of which groups happen to
    # be present in the current filter selection.
    #
    # Sort groups so named codes appear before the '—' (unknown) catch-all, giving a
    # cleaner legend order regardless of pandas sort order.
    groups = sorted(
        filtered.groupby(group_col, observed=True),
        key=lambda kv: ('zzz' if str(kv[0]) in ('—', '') else str(kv[0]).lower()),
    )
    for group_name, group_df in groups:
        color = palette[abs(hash(str(group_name))) % len(palette)]

        # Build customdata for this group
        group_customdata = _build_customdata(group_df)

        # Label: omit generic "Defect:" prefix for verification / panel modes where
        # the code itself is already descriptive (CU22, Panel_30 …).
        if config.color_mode == 'by_verification':
            legend_name = f"{group_name}  ({len(group_df)})"
        else:
            legend_name = f"Defect: {group_name}  ({len(group_df)})"

        fig.add_trace(go.Scattergl(
            x=group_df['ALIGNED_X'],
            y=group_df['ALIGNED_Y'],
            mode='markers',
            marker=dict(
                color=color,
                symbol=marker_config['symbol'],
                size=marker_config['size'],
                line=marker_config['line'],
            ),
            name=legend_name,
            legendgroup=f"defect_{group_name}",
            showlegend=True,
            customdata=group_customdata,
            hovertemplate=hover_template,
        ))


# ---------------------------------------------------------------------------
# Layout configuration
# ---------------------------------------------------------------------------



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

    fig.add_trace(go.Scattergl(
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


