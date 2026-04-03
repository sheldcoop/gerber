"""
views/panel_overview.py — Full panel CAM viewer with hierarchy boundaries.

Displays the complete ODB++ panel layout with:
- Full panel SVG (all units composited)
- Hierarchy boundaries (panel → cluster → unit)
- Panel information (size, grid layout, unit count)
"""

import streamlit as st
import plotly.graph_objects as go
from gerber_renderer import save_render_cache, build_panel_svg


def _get_cluster_positions(panel_layout):
    """Return list of (x0, y0, x1, y1) for each cluster in display coords."""
    sh = panel_layout.step_hierarchy
    uw, uh = panel_layout.unit_bounds
    raw_positions = getattr(panel_layout, 'unit_positions_raw', [])
    disp_positions = panel_layout.unit_positions
    if not raw_positions or not disp_positions:
        return []

    shift_x = disp_positions[0][0] - raw_positions[0][0]
    shift_y = disp_positions[0][1] - raw_positions[0][1]

    # Find the panel→cluster repeats and identify the cluster step name
    panel_children = sh.get('panel', [])
    cluster_step = None
    for sr in panel_children:
        child = sr.child_step.lower()
        if 'cluster' in child or 'qtr' in child:
            cluster_step = child
            break
    if cluster_step is None:
        return []

    # Compute extent of one cluster in its own local coordinate space
    cluster_children = sh.get(cluster_step, [])
    if not cluster_children:
        return []

    cu_positions = []
    for sr in cluster_children:
        for iy in range(sr.ny):
            for ix in range(sr.nx):
                cu_positions.append((sr.x + ix * sr.dx, sr.y + iy * sr.dy))
    if not cu_positions:
        return []

    c_min_x = min(p[0] for p in cu_positions)
    c_min_y = min(p[1] for p in cu_positions)
    c_max_x = max(p[0] for p in cu_positions) + uw
    c_max_y = max(p[1] for p in cu_positions) + uh

    # Emit cluster bounding boxes in panel display space
    result = []
    for sr in panel_children:
        if sr.child_step.lower() == cluster_step:
            for iy in range(sr.ny):
                for ix in range(sr.nx):
                    raw_x = sr.x + ix * sr.dx
                    raw_y = sr.y + iy * sr.dy
                    result.append((
                        raw_x + c_min_x + shift_x,
                        raw_y + c_min_y + shift_y,
                        raw_x + c_max_x + shift_x,
                        raw_y + c_max_y + shift_y,
                    ))
    return result


def _build_base_figure(panel_layout) -> go.Figure:
    """Build empty Plotly figure with correct axes and PCB background."""
    pw = panel_layout.panel_width
    ph = panel_layout.panel_height
    fig = go.Figure()
    fig.update_layout(
        xaxis=dict(
            range=[-10, pw + 10], scaleanchor='y', scaleratio=1,
            showgrid=False, zeroline=False, color='#aaa',
        ),
        yaxis=dict(
            range=[-10, ph + 10], showgrid=False, zeroline=False, color='#aaa',
        ),
        plot_bgcolor='#1a2a1a',
        paper_bgcolor='#111a11',
        margin=dict(l=0, r=0, t=24, b=0),
        height=720,
        showlegend=False,
    )
    return fig


def _add_background(fig, pw, ph) -> None:
    """Add PCB carrier and panel frame shapes."""
    fig.add_shape(
        type="rect",
        x0=-8, y0=-8, x1=pw + 8, y1=ph + 8,
        fillcolor="#2B3A2B",
        line=dict(color="#1a2a1a", width=1),
        layer="below",
    )
    fig.add_shape(
        type="rect",
        x0=0, y0=0, x1=pw, y1=ph,
        fillcolor="rgba(0,120,220,0.06)",
        line=dict(color="rgba(0,140,255,0.8)", width=2),
        layer="below",
    )


def _add_hierarchy_shapes(fig, panel_layout) -> None:
    """Add cluster and unit boundary shapes with labels."""
    uw, uh = panel_layout.unit_bounds

    # Cluster boundaries (amber dashed)
    cluster_bounds = _get_cluster_positions(panel_layout)
    for i, (x0, y0, x1, y1) in enumerate(cluster_bounds):
        fig.add_shape(
            type="rect",
            x0=x0, y0=y0, x1=x1, y1=y1,
            fillcolor="rgba(255,180,0,0.06)",
            line=dict(color="rgba(255,180,0,0.7)", width=1.5, dash="dash"),
            layer="below",
        )
        fig.add_annotation(
            x=(x0 + x1) / 2, y=y1 - 1,
            text=f"C-{i + 1:02d}",
            showarrow=False,
            font=dict(size=10, color="rgba(255,180,0,0.9)"),
            bgcolor="rgba(0,0,0,0.4)",
            borderpad=2,
        )

    # Unit boundaries (green)
    for i, (ux, uy) in enumerate(panel_layout.unit_positions):
        fig.add_shape(
            type="rect",
            x0=ux, y0=uy, x1=ux + uw, y1=uy + uh,
            fillcolor="rgba(0,200,120,0.05)",
            line=dict(color="rgba(0,220,130,0.5)", width=0.8),
            layer="below",
        )
        fig.add_annotation(
            x=ux + uw / 2, y=uy + uh / 2,
            text=f"U-{i + 1:02d}",
            showarrow=False,
            font=dict(size=8, color="rgba(0,220,130,0.7)"),
        )


def render_panel_overview(parsed, aoi, align_args):
    st.markdown("### 🏭 Panel Layout Viewer")
    st.caption(
        "View the complete ODB++ panel with step-repeat hierarchy boundaries. "
        "Shows all units composited from the selected layer."
    )

    rendered_odb = st.session_state.get('rendered_odb')
    if not rendered_odb or not rendered_odb.panel_layout:
        st.info("⬆️ Upload an ODB++ archive (.tgz) to view the panel layout.")
        return

    pl = rendered_odb.panel_layout
    pw = pl.panel_width
    ph = pl.panel_height
    uw, uh = pl.unit_bounds

    # Panel info
    info_cols = st.columns(3)
    with info_cols[0]:
        st.metric("Panel", f"{pw:.1f} × {ph:.1f} mm")
    with info_cols[1]:
        st.metric("Layout", f"{pl.cols} × {pl.rows} grid ({pl.total_units} units)")
    with info_cols[2]:
        st.metric("Unit", f"{uw:.2f} × {uh:.2f} mm")

    show_hierarchy = st.checkbox("Show Hierarchy Boundaries", value=True, key="show_hierarchy_bounds")

    # ── Figure ───────────────────────────────────────────────────────────────
    fig = _build_base_figure(pl)
    _add_background(fig, pw, ph)
    if show_hierarchy:
        _add_hierarchy_shapes(fig, pl)

    # ── CAM layer SVG ────────────────────────────────────────────────────────
    all_checked = [
        ln for ln in rendered_odb.layers
        if st.session_state.get(f"vis_{ln}", False)
    ]
    want_layer = all_checked[0] if all_checked else None

    if want_layer:
        lyr = rendered_odb.layers[want_layer]
        if not lyr.panel_svg_data_url:
            with st.spinner(f"Building panel image for {want_layer}..."):
                try:
                    lyr.panel_svg_data_url = build_panel_svg(lyr.svg_string, pl)
                except Exception:
                    pass
                tgz_b = st.session_state.get('_tgz_bytes_for_cache')
                tgz_d = st.session_state.get('_tgz_digest')
                if (tgz_b or tgz_d) and lyr.panel_svg_data_url:
                    save_render_cache(rendered_odb, digest=tgz_d,
                                      tgz_bytes=tgz_b if not tgz_d else None)
        if lyr.panel_svg_data_url:
            fig.update_layout(images=[dict(
                source=lyr.panel_svg_data_url,
                xref="x", yref="y",
                x=0, y=ph,
                sizex=pw, sizey=ph,
                sizing="stretch", layer="below", opacity=1.0,
            )])
            extra = len(all_checked) - 1
            if extra > 0:
                st.caption(
                    f"Panel image: **{want_layer}**"
                    f" (+ {extra} more selected — panel view shows one layer at a time)"
                )
    else:
        st.caption("☝️ Select a layer in the sidebar to display the panel image.")

    if show_hierarchy:
        st.markdown(
            "<small>🔵 Panel frame &nbsp;&nbsp; 🟠 Cluster boundaries &nbsp;&nbsp;"
            " 🟢 Unit boundaries</small>",
            unsafe_allow_html=True,
        )

    st.plotly_chart(
        fig,
        width='stretch',
        config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False},
    )
