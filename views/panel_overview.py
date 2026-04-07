"""
views/panel_overview.py — Full panel CAM viewer with hierarchy boundaries.

Displays the complete ODB++ panel layout with:
- Full panel PNG (rasterized for fast rendering)
- Hierarchy boundaries (panel → cluster → unit)
- Panel information (size, grid layout, unit count)

Performance optimizations:
- Converts SVG to PNG for 10x faster Plotly rendering
- Uses SVG <use> tiling as intermediate (50KB vs 5MB)
- Limits unit labels to 50 units max (prevents 100+ Plotly annotations)
- Caches both SVG and PNG builds to disk + memory
"""

import streamlit as st
import plotly.graph_objects as go
from gerber_renderer import save_render_cache, build_panel_svg


def _svg_to_png_data_url(svg_data_url: str, width_mm: float, height_mm: float, pixels_per_mm: int = 8) -> str:
    """
    Convert SVG data URL to PNG using Plotly + kaleido (already installed).
    
    Args:
        svg_data_url: base64-encoded SVG data URL
        width_mm: panel width in mm
        height_mm: panel height in mm
        pixels_per_mm: resolution (default 8 = good quality)
    
    Returns:
        PNG data URL or empty string on failure
    """
    try:
        import base64
        import plotly.graph_objects as go
        
        if not svg_data_url:
            return ''
        
        # Calculate target dimensions
        target_width = int(width_mm * pixels_per_mm)
        target_height = int(height_mm * pixels_per_mm)
        
        # Create a plotly figure with the SVG embedded
        fig = go.Figure()
        fig.add_layout_image(
            dict(
                source=svg_data_url,
                xref="x", yref="y",
                x=0, y=height_mm,
                sizex=width_mm, sizey=height_mm,
                sizing="stretch", layer="below"
            )
        )
        fig.update_layout(
            xaxis=dict(range=[0, width_mm], showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(range=[0, height_mm], showgrid=False, zeroline=False, showticklabels=False, scaleanchor='x'),
            margin=dict(l=0, r=0, t=0, b=0),
            plot_bgcolor='#060A06',
            paper_bgcolor='#060A06',
            width=target_width,
            height=target_height,
        )
        
        # Convert to PNG using kaleido (bundled with plotly/kaleido package)
        png_bytes = fig.to_image(format="png", width=target_width, height=target_height)
        
        # Encode as PNG data URL
        png_b64 = base64.b64encode(png_bytes).decode('utf-8')
        return f'data:image/png;base64,{png_b64}'
    
    except Exception as e:
        st.error(f"❌ PNG conversion failed: {str(e)}")
        st.info("💡 Make sure kaleido is installed: pip install kaleido")
        return ''
    except Exception as e:
        st.warning(f"⚠️ PNG conversion failed: {e}")
        return ''


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


def _add_hierarchy_shapes(fig, panel_layout, max_labels: int = 50) -> None:
    """Add cluster and unit boundary shapes with labels (optimized for large panels)."""
    uw, uh = panel_layout.unit_bounds
    total_units = panel_layout.total_units

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
        # Only label clusters if not too many
        if len(cluster_bounds) <= 8:
            fig.add_annotation(
                x=(x0 + x1) / 2, y=y1 - 1,
                text=f"C-{i + 1:02d}",
                showarrow=False,
                font=dict(size=10, color="rgba(255,180,0,0.9)"),
                bgcolor="rgba(0,0,0,0.4)",
                borderpad=2,
            )

    # Unit boundaries (green) - limit labels for large panels
    show_unit_labels = total_units <= max_labels
    for i, (ux, uy) in enumerate(panel_layout.unit_positions):
        fig.add_shape(
            type="rect",
            x0=ux, y0=uy, x1=ux + uw, y1=uy + uh,
            fillcolor="rgba(0,200,120,0.05)",
            line=dict(color="rgba(0,220,130,0.5)", width=0.8),
            layer="below",
        )
        # Only add labels for smaller panels to avoid performance issues
        if show_unit_labels:
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

    # Performance warning for large panels
    if pl.total_units > 100:
        st.info(
            f"⚠️ Large panel ({pl.total_units} units) - Unit labels disabled for performance. "
            "First render may take 10-30 seconds while building composite image."
        )
    
    # Resolution selector
    col1, col2 = st.columns([3, 1])
    with col1:
        show_hierarchy = st.checkbox("Show Hierarchy Boundaries", value=True, key="show_hierarchy_bounds")
    with col2:
        png_quality = st.selectbox(
            "PNG Quality",
            options=[6, 8, 10, 12],
            index=1,  # default 8 pixels/mm
            help="Higher = better quality but larger file size. 8 pixels/mm is recommended."
        )

    # ── Figure ───────────────────────────────────────────────────────────────
    fig = _build_base_figure(pl)
    _add_background(fig, pw, ph)
    if show_hierarchy:
        _add_hierarchy_shapes(fig, pl)

    # ── CAM layer PNG (properly cached to disk) ─────────────────────────────
    all_checked = [
        ln for ln in rendered_odb.layers
        if st.session_state.get(f"vis_{ln}", False)
    ]
    want_layer = all_checked[0] if all_checked else None

    if want_layer:
        lyr = rendered_odb.layers[want_layer]
        needs_save = False
        
        # Step 1: Build SVG if not cached (SVG is intermediate step for PNG)
        if not lyr.panel_svg_data_url:
            with st.spinner(f"⏳ Building panel SVG for {want_layer}... (step 1/2)"):
                try:
                    lyr.panel_svg_data_url = build_panel_svg(lyr.svg_string, pl)
                    needs_save = True
                except Exception as e:
                    st.error(f"❌ Failed to build panel SVG: {e}")
        
        # Step 2: Convert SVG to PNG if not cached (PNG for fast Plotly rendering)
        if lyr.panel_svg_data_url and not lyr.panel_png_data_url:
            with st.spinner(f"⏳ Converting to PNG for {want_layer}... (step 2/2 - faster display)"):
                lyr.panel_png_data_url = _svg_to_png_data_url(lyr.panel_svg_data_url, pw, ph, png_quality)
                if lyr.panel_png_data_url:
                    needs_save = True
        
        # Step 3: Save to disk cache (persists PNG + SVG)
        if needs_save:
            tgz_b = st.session_state.get('_tgz_bytes_for_cache')
            tgz_d = st.session_state.get('_tgz_digest')
            if tgz_b or tgz_d:
                save_render_cache(rendered_odb, digest=tgz_d, tgz_bytes=tgz_b if not tgz_d else None)
                st.success(f"✅ Cached to disk for instant future loads")
        
        # Step 4: Display PNG ONLY (no SVG fallback for Panel Overview)
        if lyr.panel_png_data_url:
            fig.update_layout(images=[dict(
                source=lyr.panel_png_data_url,
                xref="x", yref="y",
                x=0, y=ph,
                sizex=pw, sizey=ph,
                sizing="stretch", layer="below", opacity=1.0,
            )])
            extra = len(all_checked) - 1
            if extra > 0:
                st.caption(
                    f"🖼️ Panel (PNG): **{want_layer}** "
                    f"(+{extra} more selected — showing one layer)"
                )
            else:
                st.caption(f"🖼️ Panel (PNG): **{want_layer}**")
        else:
            st.error("❌ PNG conversion failed. Cannot display panel overview without PNG.")
            st.info("💡 Try installing PNG conversion libraries or check error messages above.")
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
