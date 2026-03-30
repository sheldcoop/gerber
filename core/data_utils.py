import streamlit as st
import pandas as pd
from clustering import compute_clusters, get_cluster_summary, get_cluster_hull_coords
from alignment import calculate_geometry, INTER_UNIT_GAP

@st.cache_data(show_spinner=False)
def compute_clusters_cached(_df_hash: str, _df: pd.DataFrame, eps: float, min_samples: int):
    """Run DBSCAN + summary + all hull coords. Cached by df hash + params."""
    clustered = compute_clusters(_df, eps=eps, min_samples=min_samples)
    summary = get_cluster_summary(clustered)
    hulls = {}
    if not summary.empty:
        for _, crow in summary.iterrows():
            h = get_cluster_hull_coords(clustered, crow['cluster_id'])
            if h:
                hulls[crow['cluster_id']] = (h, crow['defect_count'])
    return clustered, summary, hulls


@st.cache_data(show_spinner=False)
def compute_panel_shapes(rows: int, cols: int, gap_x: float, gap_y: float) -> list:
    """Pre-compute all unit cell shape dicts. Cached per grid geometry."""
    ctx = calculate_geometry(rows, cols, gap_x, gap_y)
    shapes = []
    for _, (q_ox, q_oy) in ctx.quadrant_origins.items():
        for r in range(rows):
            for c in range(cols):
                ux = q_ox + INTER_UNIT_GAP + c * ctx.stride_x
                uy = q_oy + INTER_UNIT_GAP + r * ctx.stride_y
                shapes.append(dict(
                    type="rect",
                    x0=ux, y0=uy,
                    x1=ux + ctx.cell_width, y1=uy + ctx.cell_height,
                    fillcolor="rgba(0,180,100,0.07)",
                    line=dict(color="rgba(0,220,130,0.5)", width=0.8),
                    layer="below",
                ))
    return shapes


@st.cache_data(show_spinner=False)
def compute_cm_geometry(
    unit_positions: tuple,       # tuple of (x, y) — ODB++ display (panel-absolute) coords
    first_layer_bounds: tuple,   # (min_x, min_y, max_x, max_y) of CAM layer in local space
) -> tuple:
    """Return (origins_dict, cell_w, cell_h). Cached per unique TGZ layout.

    origins_dict maps (row_index, col_index) → (origin_x, origin_y) where:
      - row_index / col_index are 0-based sorted position indices
      - origin_x/y = the unit's display position (step origin in panel space)

    To align a defect: ALIGNED = (X_MM - origin_x, Y_MM - origin_y)
    Result is in [0, cell_w] × [0, cell_h], matching the CAM SVG in Plotly.
    """
    cam_min_x, cam_min_y, cam_max_x, cam_max_y = first_layer_bounds
    cell_w = cam_max_x - cam_min_x
    cell_h = cam_max_y - cam_min_y
    uniq_x = sorted(set(round(x, 2) for x, _ in unit_positions))
    uniq_y = sorted(set(round(y, 2) for _, y in unit_positions))
    # Origin = display position only — NO cam_min offset.
    # AOI measures from the step origin; cam_min offset must NOT be subtracted.
    origins = {
        (ri, ci): (uniq_x[ci], uniq_y[ri])
        for ri in range(len(uniq_y))
        for ci in range(len(uniq_x))
    }
    return origins, cell_w, cell_h


def filter_aoi_cm(
    _df: pd.DataFrame,
    buildup_filter: tuple,
    side_filter: tuple,
) -> pd.DataFrame:
    """Scope-filter AOI defects for Commonality. Cached by filter combo."""
    src = _df.copy()
    if buildup_filter and 'BUILDUP' in src.columns:
        src = src[src['BUILDUP'].isin(buildup_filter)]
    if 'SIDE' in src.columns:
        if 'Front' in side_filter and 'Back' not in side_filter:
            src = src[src['SIDE'] == 'F']
        elif 'Back' in side_filter and 'Front' not in side_filter:
            src = src[src['SIDE'] == 'B']
    return src
