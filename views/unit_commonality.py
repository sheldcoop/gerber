import streamlit as st
import numpy as np
import plotly.graph_objects as go
from typing import Any, Tuple, List

from core.data_utils import (
    compute_cm_geometry, filter_aoi_cm, _align_defects, _compute_pad_fingerprint,
    _SEV_DOT_SCALE, _SEV_LABEL, _SEV_COLOR,
)
from core.svg_utils import build_rotated_svg_url
from alignment import calculate_geometry, INTER_UNIT_GAP
from visualizer import OverlayConfig, build_defect_only_figure, _apply_layout
from export import export_current_view

_LAYER_Z = {
    'drill': 0, 'other': 1, 'paste': 2,
    'soldermask': 3, 'silkscreen': 4, 'outline': 5, 'copper': 6
}
_LAYER_OPACITY_SINGLE = {'copper': 0.95, 'drill': 0.55, 'other': 0.60}
_LAYER_OPACITY_MULTI  = {'copper': 0.90, 'drill': 0.45, 'other': 0.50}

# Distinct colors for each layer when multiple are shown simultaneously.
# Every layer gets a unique hue so 2F / 3F / 1B are immediately distinguishable.
# None of these is the raw copper '#b87333' — that keeps the single-layer look and
# would make multi-layer look the same as single-layer for the first entry.
_MULTI_LAYER_COLORS = [
    '#FF9800',  # amber   — layer 0 (e.g. 1F / top copper)
    '#2196F3',  # blue    — layer 1 (e.g. 2F / inner 1)
    '#4CAF50',  # green   — layer 2 (e.g. 3F / inner 2)
    '#9C27B0',  # purple  — layer 3
    '#00BCD4',  # cyan    — layer 4
    '#E91E63',  # pink    — layer 5
    '#CDDC39',  # lime    — layer 6
    '#F44336',  # red     — layer 7
]


def _layer_sort_key(name_lyr_pair: Tuple[str, Any]) -> int:
    return _LAYER_Z.get(name_lyr_pair[1].layer_type, 1)


def _layer_opacity(layer_name: str, lyr_type: str, multi: bool) -> float:
    slider_val = st.session_state.get(f"opacity_{layer_name}")
    if slider_val is not None:
        return float(slider_val)
    d = _LAYER_OPACITY_MULTI if multi else _LAYER_OPACITY_SINGLE
    return d.get(lyr_type, 0.70 if multi else 0.85)


def _svg_url(lyr_obj: Any, rot_deg: float, is_multi: bool,
             layer_color: str = None) -> str:
    """Reference-layer SVG url honouring the invert-polarity toggle.

    Rotating a multi-megabyte copper SVG costs ~15-20 ms and runs on every Streamlit
    rerun for every shown layer on a rotated panel. The un-rotated path is already O(1)
    (precomputed data URL). For the rotating path we memoise the result per session,
    keyed by (layer name, rotation, invert, multi, color). The cache is tied to the
    current rendered_odb object identity so it auto-resets when a new TGZ is uploaded.
    """
    invert = st.session_state.get('invert_polarity', False)
    name = getattr(lyr_obj, 'name', None)
    if (abs(rot_deg) < 0.01 and not invert and not layer_color) or name is None:
        return build_rotated_svg_url(lyr_obj, rot_deg, is_multi, invert=invert,
                                     layer_color=layer_color)

    gen = id(st.session_state.get('rendered_odb'))
    store = st.session_state.get('_cm_svg_cache')
    if store is None or store.get('gen') != gen:
        store = {'gen': gen, 'data': {}}
        st.session_state['_cm_svg_cache'] = store
    cache = store['data']

    key = (name, round(rot_deg, 1), bool(invert), bool(is_multi), layer_color)
    url = cache.get(key)
    if url is None:
        url = build_rotated_svg_url(lyr_obj, rot_deg, is_multi, invert=invert,
                                    layer_color=layer_color)
        cache[key] = url
    return url


def _place_layer_image(fig, layer_name, lyr, ref_shift, svg_rot, swap, is_multi,
                       layer_color: str = None):
    """Add one reference-design layer as a Plotly background image.

    Single source of the layer-placement + 90/270 dimension-swap convention shared by
    the empty-state, empty-layers, and defect-state overlays.

    Args:
        ref_shift:    (sx, sy) translation so the reference layer sits at the cell origin.
        svg_rot:      degrees to rotate the SVG *content* (auto angle + manual nudge).
        swap:         True for orthogonal 90/270 placement (image fills the swapped cell).
        layer_color:  Optional hex color to substitute into the SVG (used to give each
                      layer a distinct hue when multiple layers are shown together).
    """
    b = lyr.bounds
    im_w, im_h = b[2] - b[0], b[3] - b[1]
    sx, sy = ref_shift

    # Center of the layer in the standard (unswapped) coordinate system
    layer_cx = b[0] + sx + im_w / 2.0
    layer_cy = b[3] + sy - im_h / 2.0

    # If the canvas itself is swapped (e.g. panel is rotated 90 deg), the center moves
    if swap:
        final_cx, final_cy = im_h / 2.0, im_w / 2.0
    else:
        final_cx, final_cy = layer_cx, layer_cy

    # If the SVG content is rotated 90/270, its viewBox dimensions are swapped
    svg_is_swapped = round(svg_rot) % 360 in (90, 270)
    if svg_is_swapped:
        szx, szy = im_h, im_w
    else:
        szx, szy = im_w, im_h

    # Plotly anchors images at top-left by default
    x = final_cx - szx / 2.0
    y = final_cy + szy / 2.0

    fig.add_layout_image(dict(
        source=_svg_url(lyr, svg_rot, is_multi, layer_color=layer_color),
        xref="x", yref="y", x=x, y=y, sizex=szx, sizey=szy,
        sizing="stretch", layer="below",
        opacity=_layer_opacity(layer_name, lyr.layer_type, is_multi),
    ))


def _display_dims(cell_w, cell_h, angle):
    """Return the on-screen (panel-oriented) cell dimensions.

    `compute_cm_geometry` reports the unit's NATIVE (un-rotated) footprint. AOI defect
    coordinates, after subtracting the unit origin, are already in the panel frame — i.e.
    for a unit placed at 90/270 the defects span the swapped (portrait) footprint. So we
    only swap the *display* dimensions and rotate the reference DESIGN to match; the defect
    points themselves are never rotated (verified: 100% of fhr0020 defects land in the
    swapped 37.5x43.5 cell with translation alone).
    """
    return (cell_h, cell_w) if round(angle) % 360 in (90, 270) else (cell_w, cell_h)


def _render_sidebar_controls(rodb_cm_check: Any) -> List[Tuple[str, Any]]:
    """Reads the sidebar layer selection from session state and returns checked layers.

    The actual checkboxes/opacity sliders are rendered in ui/sidebar.py; here we only
    read the `vis_<layer>` session-state flags.
    """
    if not rodb_cm_check or not rodb_cm_check.layers:
        return []

    return [
        (ln, rodb_cm_check.layers[ln])
        for ln in sorted(rodb_cm_check.layers.keys())
        if st.session_state.get(f"vis_{ln}", False)
    ]


# ---------------------------------------------------------------------------
# Empty state — design preview when no AOI data is loaded
# ---------------------------------------------------------------------------

def _render_empty_state(rodb_cm_check: Any, na_checked: List[Tuple[str, Any]]) -> None:
    """Renders the TGZ design preview when no AOI defect data is loaded."""
    st.info("ℹ️ Upload AOI defect data to overlay defects on the design.")
    if not rodb_cm_check or not rodb_cm_check.layers:
        return

    if not na_checked:
        st.caption("☝️ Select a layer in the sidebar to view the design.")
        return

    _ref_lyr = next(
        (l for l in rodb_cm_check.layers.values() if l.layer_type != 'drill'),
        next(iter(rodb_cm_check.layers.values()))
    )

    if rodb_cm_check.panel_layout:
        _, cw, ch = compute_cm_geometry(
            unit_positions=tuple(rodb_cm_check.panel_layout.unit_positions),
            first_layer_bounds=tuple(_ref_lyr.bounds),
            unit_bounds=rodb_cm_check.panel_layout.unit_bounds,
        )
    else:
        _rb = _ref_lyr.bounds
        cw = _rb[2] - _rb[0]
        ch = _rb[3] - _rb[1]

    _ref_b  = _ref_lyr.bounds
    _ref_shift = (-_ref_b[0], -_ref_b[1])
    _is_multi = len(na_checked) > 1
    _sorted = sorted(na_checked, key=_layer_sort_key)

    _unit_angle = getattr(rodb_cm_check.panel_layout, 'dominant_angle', 0.0) if rodb_cm_check.panel_layout else 0.0
    _rot_deg = float(round(_unit_angle) % 360)
    _swap = _rot_deg in (90.0, 270.0)
    # At 90/270 the unit footprint is rotated — swap canvas dims once (not per layer).
    if _swap:
        cw, ch = ch, cw

    fig = go.Figure()
    for _idx, (_n, _l) in enumerate(_sorted):
        _lc = _MULTI_LAYER_COLORS[_idx % len(_MULTI_LAYER_COLORS)] if _is_multi else None
        _place_layer_image(fig, _n, _l, _ref_shift, _rot_deg, _swap, _is_multi, layer_color=_lc)

    _lbl = " + ".join(n for n, _ in na_checked)
    _add_dim_annotations(fig, cw, ch, _lbl)
    fig.update_layout(
        xaxis=dict(range=[-1, cw + 1], scaleanchor='y', scaleratio=1,
                   showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(range=[-1, ch + 1], showgrid=False,
                   zeroline=False, showticklabels=False),
        plot_bgcolor='#000000', paper_bgcolor='#000000',
        font=dict(color='#cccccc'),
        margin=dict(l=0, r=0, t=36, b=0), height=800,
    )
    st.plotly_chart(fig, width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})


# ---------------------------------------------------------------------------
# Info card — bottom-right corner (replaces W/H top/left labels)
# ---------------------------------------------------------------------------

def _add_dim_annotations(fig: go.Figure, cw: float, ch: float,
                          layer_label: str = None, panel_label: str = None) -> None:
    """Render a compact info card in the lower-right corner of the plot.

    Shows: W × H dimensions, active layer name, and active panel(s).
    Uses paper coordinates so it never overlaps the defect data.
    """
    lines = [f"📐 {cw:.2f} × {ch:.2f} mm"]
    if layer_label:
        lines.append(f"🗂 {layer_label}")
    if panel_label:
        lines.append(f"🖥 {panel_label}")
    text = "<br>".join(lines)

    fig.add_annotation(
        x=1.02, y=0.0,
        xref="paper", yref="paper",
        xanchor="left", yanchor="bottom",
        text=text,
        showarrow=False,
        bgcolor="rgba(6,18,6,0.82)",
        bordercolor="rgba(0,200,100,0.45)",
        borderwidth=1,
        borderpad=6,
        font=dict(color="rgba(0,220,130,0.95)", size=11, family="monospace"),
        align="left",
    )


# ---------------------------------------------------------------------------
# Unit selection (multiselect + quadrant quick-select)
# ---------------------------------------------------------------------------

def _select_units(rodb, aoi):
    """Render the unit multiselect + Q1-Q4 buttons. Returns (sel_units, all_pairs,
    q_rows, q_cols, gap_x, gap_y)."""
    q_rows = int(st.session_state.get('quad_rows_input', 6))
    q_cols = int(st.session_state.get('quad_cols_input', 6))
    gap_x  = float(st.session_state.get('dyn_gap_x_input', 5.0))
    gap_y  = float(st.session_state.get('dyn_gap_y_input', 3.5))

    if rodb and rodb.panel_layout:
        _rp  = rodb.panel_layout.unit_positions
        _uxs = sorted(set(round(x, 2) for x, _ in _rp))
        _uys = sorted(set(round(y, 2) for _, y in _rp))
        all_pairs = [(ri, ci) for ri in range(len(_uys)) for ci in range(len(_uxs))]
        q_rows = max(1, len(_uys) // 2)
        q_cols = max(1, len(_uxs) // 2)
    else:
        _aup = (
            aoi.all_defects[['UNIT_INDEX_Y', 'UNIT_INDEX_X']]
            .dropna().drop_duplicates()
            .sort_values(['UNIT_INDEX_Y', 'UNIT_INDEX_X'])
            .values.tolist()
        )
        all_pairs = [(int(r), int(c)) for r, c in _aup]
    all_labels = [f"({r},{c})" for r, c in all_pairs]

    def _quad(r, c):
        qr, qc = r // q_rows, c // q_cols
        return {(0, 0): 'Q2', (0, 1): 'Q3', (1, 0): 'Q1', (1, 1): 'Q4'}.get((qr, qc), 'Other')

    _q_lbls = {q: [l for (r, c), l in zip(all_pairs, all_labels) if _quad(r, c) == q]
               for q in ('Q1', 'Q2', 'Q3', 'Q4')}

    if 'cm_multiselect' not in st.session_state:
        st.session_state['cm_multiselect'] = all_labels

    def _set(labels):
        def cb():
            st.session_state['cm_multiselect'] = [l for l in labels if l in all_labels]
        return cb

    _cols = st.columns(6, gap="small")
    _cols[0].button("ALL",   key="cm_all",   on_click=_set(all_labels),     width="stretch", type="primary")
    _cols[1].button("Q1",    key="cm_q1",    on_click=_set(_q_lbls['Q1']),  width="stretch")
    _cols[2].button("Q2",    key="cm_q2",    on_click=_set(_q_lbls['Q2']),  width="stretch")
    _cols[3].button("Q3",    key="cm_q3",    on_click=_set(_q_lbls['Q3']),  width="stretch")
    _cols[4].button("Q4",    key="cm_q4",    on_click=_set(_q_lbls['Q4']),  width="stretch")
    _cols[5].button("Clear", key="cm_clear", on_click=_set([]),             width="stretch")

    _cur = [l for l in st.session_state.get('cm_multiselect', []) if l in all_labels]
    if _cur != st.session_state.get('cm_multiselect'):
        st.session_state['cm_multiselect'] = _cur

    _sel_labels = st.multiselect(
        "Selected units (row, col)", options=all_labels, key='cm_multiselect',
        help="Choose which units' defects to superimpose. Use the quick-select buttons above for bulk selection.",
    )

    sel_units = []
    for _lbl in _sel_labels:
        try:
            _r, _c = _lbl.strip('()').split(',')
            sel_units.append((int(_r.strip()), int(_c.strip())))
        except Exception:
            pass

    st.caption(f"**{len(sel_units)}** / {len(all_pairs)} units selected")
    st.divider()
    return sel_units, all_pairs, q_rows, q_cols, gap_x, gap_y


# ---------------------------------------------------------------------------
# Origin geometry for the reference unit cell
# ---------------------------------------------------------------------------

def _compute_origins(rodb, q_rows, q_cols, gap_x, gap_y):
    """Returns (origins_dict, cell_w, cell_h, first_layer_or_None)."""
    if rodb and rodb.panel_layout and rodb.layers:
        first_lyr = next(
            (l for l in rodb.layers.values() if l.layer_type != 'drill'),
            next(iter(rodb.layers.values()))
        )
        origins, cw, ch = compute_cm_geometry(
            unit_positions=tuple(rodb.panel_layout.unit_positions),
            first_layer_bounds=tuple(first_lyr.bounds),
            unit_bounds=rodb.panel_layout.unit_bounds,
        )
        return origins, cw, ch, first_lyr

    # No TGZ: synthesise origins from the geometry context.
    ctx = calculate_geometry(q_rows, q_cols, gap_x, gap_y)
    _pos: list = []
    for _qox, _qoy in ctx.quadrant_origins.values():
        for _rr in range(q_rows):
            for _cc in range(q_cols):
                _pos.append((
                    _qox + INTER_UNIT_GAP + _cc * ctx.stride_x,
                    _qoy + INTER_UNIT_GAP + _rr * ctx.stride_y,
                ))
    _xs = sorted(set(round(x, 2) for x, _ in _pos))
    _ys = sorted(set(round(y, 2) for _, y in _pos))
    origins = {(ri, ci): (_xs[ci], _ys[ri])
               for ri in range(len(_ys)) for ci in range(len(_xs))}
    return origins, ctx.cell_width, ctx.cell_height, None


# ---------------------------------------------------------------------------
# Reference design layer overlay
# ---------------------------------------------------------------------------

def _overlay_reference_layers(fig, rodb, svg_rot, swap_angle, first_lyr, cfg):
    """Draw checked reference design layers under the defect cloud. Returns the
    active (top-most) layer name, or None.

    svg_rot: degrees to rotate the SVG content (auto panel angle + manual nudge).
    swap_angle: the auto panel angle (orthogonal) that decides the 90/270 placement swap.
    """
    if not (rodb and rodb.layers and first_lyr):
        return None

    cam_layers = [n for n in rodb.layers if st.session_state.get(f"vis_{n}", False)]
    is_multi = len(cam_layers) > 1
    pairs = [(ln, rodb.layers[ln]) for ln in cam_layers if rodb.layers.get(ln)]
    pairs.sort(key=_layer_sort_key)
    if not pairs:
        return None
    active = pairs[-1][0]

    _ref_b = first_lyr.bounds
    ref_shift = (-_ref_b[0], -_ref_b[1])
    swap = round(swap_angle) % 360 in (90, 270)
    for _idx, (_ln, _lyr) in enumerate(pairs):
        _lc = _MULTI_LAYER_COLORS[_idx % len(_MULTI_LAYER_COLORS)] if is_multi else None
        _place_layer_image(fig, _ln, _lyr, ref_shift, svg_rot, swap, is_multi, layer_color=_lc)
    _apply_layout(fig, cfg)
    return active


# ---------------------------------------------------------------------------
# Fingerprint figure
# ---------------------------------------------------------------------------

def _build_fingerprint_figure(fp_df, n_sel_units):
    fig = go.Figure()
    for _sev in (3, 2, 1, 0):
        rows = fp_df[fp_df['severity'] == _sev]
        if rows.empty:
            continue
        sizes = (rows['unit_count'].clip(upper=n_sel_units)
                 .apply(lambda n: _SEV_DOT_SCALE[_sev] + n * 1.2))
        fig.add_trace(go.Scatter(
            x=rows['cx'], y=rows['cy'], mode='markers', name=_SEV_LABEL[_sev],
            marker=dict(color=_SEV_COLOR[_sev], size=sizes, opacity=0.82,
                        line=dict(color='rgba(0,0,0,0.4)', width=0.8)),
            customdata=rows[['unit_count', 'unit_pct', 'top_verif', 'all_verif',
                             'top_type', 'buildup', 'defect_count']].values,
            hovertemplate=(
                "<b>Verification: %{customdata[2]}</b><br>"
                "All codes at this site: %{customdata[3]}<br>"
                "Defect type: %{customdata[4]}<br>"
                "Units hit: <b>%{customdata[0]}</b> (%{customdata[1]}%)<br>"
                "Total defects at site: %{customdata[6]}<br>"
                "Buildup: %{customdata[5]}<br>"
                "X: %{x:.2f} mm  Y: %{y:.2f} mm"
                "<extra></extra>"
            ),
        ))
    return fig


# ---------------------------------------------------------------------------
# Metrics + fault-site table
# ---------------------------------------------------------------------------

def _render_metrics(fp_df, sel_units, cm_plot):
    c1, c2, c3 = st.columns(3)
    if not fp_df.empty:
        systemic = int((fp_df['unit_pct'] >= 50.0).sum())
        worst = fp_df.iloc[0]
        crit_sys = int(((fp_df['severity'] == 3) & (fp_df['unit_pct'] >= 50.0)).sum())
        c1.metric("Systemic Fault Sites (≥ 50 % units)", systemic,
                  help="Fault sites where ≥ 50 % of selected units had a defect. These are process faults, not random.")
        c2.metric("Worst Site Hit Rate", f"{worst['unit_pct']:.0f}% — {worst['top_verif']}",
                  help="The fault site with the highest unit hit % and the top verification code driving it.")
        c3.metric("Critical + Systemic", crit_sys,
                  delta=f"of {len(fp_df)} total fault sites", delta_color="inverse",
                  help="Critical-severity fault sites also failing on ≥ 50 % of units — the highest-priority items to fix.")
    else:
        c1.metric("Units Selected", len(sel_units))
        c2.metric("Defects Shown", len(cm_plot))
        c3.metric("Avg / Unit", f"{len(cm_plot)/max(len(sel_units),1):.1f}")


def _fault_site_table(fp_df):
    if fp_df.empty:
        return
    st.divider()
    st.markdown("#### 🔬 Fault Site Recurrence Fingerprint")
    st.caption(
        "Each row is a distinct fault site (defects snapped to 0.5 mm grid). "
        "**Unit hit %** = how many of the selected units had a defect here. "
        "A site at 100 % is a **systemic process fault** — it fails on every unit, every time."
    )

    systemic = fp_df[fp_df['unit_pct'] >= 80.0]
    if not systemic.empty:
        crit_sys = systemic[systemic['severity'] == 3]
        if not crit_sys.empty:
            st.error(
                f"🚨 **{len(crit_sys)} Critical fault site(s) are failing on ≥ 80 % of units.** "
                "This is a systemic process fault — not random defects."
            )
        else:
            st.warning(f"⚠️ **{len(systemic)} fault site(s) are failing on ≥ 80 % of units.**")

    disp = fp_df.copy()
    disp.insert(0, 'Rank', range(1, len(disp) + 1))
    disp = disp.rename(columns={
        'cx': 'X (mm)', 'cy': 'Y (mm)', 'unit_count': 'Units Hit', 'unit_pct': 'Hit %',
        'severity_label': 'Severity', 'top_verif': 'Top Verification',
        'all_verif': 'All Verif. Codes', 'top_type': 'Top Defect Type',
        'buildup': 'Buildup(s)', 'defect_count': 'Total Defects',
    }).drop(columns=['severity'])

    def _colour_sev(val):
        c = {'Critical': '#FF3B3B', 'High': '#FF9900',
             'Medium': '#FFD700', 'Low': '#66BB6A'}.get(val, '')
        return f'color: {c}; font-weight: bold' if c else ''

    styled = (
        disp.style
        .applymap(_colour_sev, subset=['Severity'])
        .background_gradient(subset=['Hit %'], cmap='Reds', vmin=0, vmax=100)
        .format({'X (mm)': '{:.2f}', 'Y (mm)': '{:.2f}', 'Hit %': '{:.1f}'})
    )
    st.dataframe(styled, use_container_width=True, height=320)


# ---------------------------------------------------------------------------
# Defect state — the interactive superposition view
# ---------------------------------------------------------------------------

def _render_defect_state(rodb, aoi, align_args):
    sel_units, all_pairs, q_rows, q_cols, gap_x, gap_y = _select_units(rodb, aoi)
    if not sel_units:
        st.info("Select at least one unit to display.")
        return

    origins, cell_w, cell_h, first_lyr = _compute_origins(rodb, q_rows, q_cols, gap_x, gap_y)

    # ── Scope — read from global Analysis Scope (sidebar) ───────────────────
    # Buildup: use whatever is active in the global scope bar (BU-01, BU-02 …)
    bu = st.session_state.get('buildup_filter_select', aoi.buildup_numbers)

    # Panel filter — read from the global Analysis Scope (scope_panel_sel).
    # When >1 panel is selected the color mode is auto-set to by_panel below.
    panel_filter = st.session_state.get('panel_filter_select', None)
    has_multi_panel = bool(panel_filter and len(panel_filter) > 1)

    # Scope filter (buildup/side) then restrict to the selected units.
    side = st.session_state.get('scope_side_sel', ['Front', 'Back'])
    src = filter_aoi_cm(
        aoi.all_defects,
        tuple(sorted(bu)) if bu else (),
        tuple(sorted(side)),
    ).copy()

    # Apply panel filter from global scope.
    if panel_filter is not None and 'PANEL_ID' in src.columns:
        src = src[src['PANEL_ID'].isin(panel_filter)].copy()

    if 'UNIT_INDEX_Y' not in src.columns or 'UNIT_INDEX_X' not in src.columns:
        st.error("Cannot align defects: AOI data is missing UNIT_INDEX_X / UNIT_INDEX_Y columns.")
        return

    src['_ukey'] = list(zip(src['UNIT_INDEX_Y'].astype(int), src['UNIT_INDEX_X'].astype(int)))
    src = src[src['_ukey'].isin(set(sel_units))].copy()
    src.drop(columns=['_ukey'], inplace=True)

    if src.empty:
        st.info("No defects found for the selected units / scope filters.")
        _render_empty_layers(rodb, first_lyr, cell_w, cell_h)
        return

    if 'X_MM' not in src.columns or 'Y_MM' not in src.columns:
        st.error("Cannot align defects: AOI data is missing X_MM / Y_MM columns.")
        return

    # Unit placement angle — cumulative rotation from the step hierarchy (handles
    # cluster-level and unit-level rotation identically; 0 when unrotated).
    dom_angle = getattr(rodb.panel_layout, 'dominant_angle', 0.0) if (rodb and rodb.panel_layout) else 0.0

    pairs = list(zip(src['UNIT_INDEX_Y'].astype(int), src['UNIT_INDEX_X'].astype(int)))
    ox_arr = [origins.get(p, (0.0, 0.0))[0] for p in pairs]
    oy_arr = [origins.get(p, (0.0, 0.0))[1] for p in pairs]
    off_x = align_args.get('manual_offset_x', 0.0)
    off_y = align_args.get('manual_offset_y', 0.0)

    # Optional manual rotation override (rotates the entire view: CAD, defects, and canvas).
    with st.form("cm_rotation_form", border=False):
        manual_rot = st.number_input(
            "View rotation override (°)", min_value=0.0, max_value=360.0,
            value=0.0, step=0.5, format="%.1f", key='cm_rotation_deg',
            help="Rotate the entire view (CAD background, defects, and canvas dimensions). Useful if you want to look at the unit vertically instead of horizontally.",
        )
        st.form_submit_button("Apply rotation", use_container_width=True)

    try:
        ax, ay = _align_defects(
            tuple(src['X_MM'].values.tolist()),
            tuple(src['Y_MM'].values.tolist()),
            tuple(ox_arr), tuple(oy_arr),
            off_x, off_y,
        )
    except ValueError as e:
        st.error(f"Cannot align defects: {e}")
        return

    # Defects are aligned in the unit's native frame (translation only). Fault-site
    # grouping below also runs in this native frame so sites are rotation-invariant.
    cm_plot = src.copy()
    cm_plot['ALIGNED_X'] = list(ax)
    cm_plot['ALIGNED_Y'] = list(ay)

    cfg = OverlayConfig()
    # Auto color-by-panel when comparing multiple panels, but only when the user
    # has not made an explicit choice.  If the sidebar colour mode is anything
    # other than the generic 'by_type' default we treat it as intentional and
    # honour it even across panels (e.g. the user explicitly picked by_verification
    # to see CU22/CU18 colours).
    user_color_mode = st.session_state.get('color_mode_select', 'by_type')
    if has_multi_panel and panel_filter and len(panel_filter) > 1:
        cfg.color_mode = user_color_mode if user_color_mode != 'by_type' else 'by_panel'
    else:
        cfg.color_mode = user_color_mode
    cfg.marker_style   = st.session_state.get('marker_style_select', 'dot')
    cfg.buildup_filter = bu
    cfg.defect_types   = st.session_state.get('defect_type_select', aoi.defect_types)
    cfg.side_filter    = 'Both'

    fp_mode = st.toggle(
        "🔬 Fault Site Fingerprint Mode", value=False, key="cm_fingerprint_toggle",
        help=("Replace raw dots with one marker per recurring fault site. "
              "Size = how many units were hit. "
              "Colour = worst defect severity (red=Critical, orange=High, yellow=Medium, green=Low). "
              "Hover to see the top verification code (e.g. CU22, SH, OP)."),
    )

    fp_df = _compute_pad_fingerprint(
        ax_tuple=tuple(cm_plot['ALIGNED_X'].tolist()),
        ay_tuple=tuple(cm_plot['ALIGNED_Y'].tolist()),
        defect_types=tuple(cm_plot['DEFECT_TYPE'].tolist() if 'DEFECT_TYPE' in cm_plot.columns
                           else ['unknown'] * len(cm_plot)),
        unit_keys=tuple(zip(cm_plot['UNIT_INDEX_Y'].astype(int).tolist(),
                            cm_plot['UNIT_INDEX_X'].astype(int).tolist())),
        buildup_vals=tuple(cm_plot['BUILDUP'].tolist() if 'BUILDUP' in cm_plot.columns else []),
        verification_vals=tuple(cm_plot['VERIFICATION'].tolist() if 'VERIFICATION' in cm_plot.columns else []),
        verif_severity_map=tuple(st.session_state.get('verif_severity_map', {}).items()),
    )

    # ── Display orientation: defects are already in the panel frame (translation), so we
    #    only swap the displayed cell dims and rotate the reference DESIGN to match. ──
    theta = round(dom_angle) % 360
    svg_rot = (theta + manual_rot) % 360  # manual nudge sits on top of the auto angle
    disp_w, disp_h = _display_dims(cell_w, cell_h, theta)
    cfg.board_bounds = (-1.0, -1.0, disp_w + 1.0, disp_h + 1.0)

    if fp_mode and not fp_df.empty:
        fig = _build_fingerprint_figure(fp_df, len(sel_units))
    else:
        fig = build_defect_only_figure(cm_plot, cfg)

    active_layer = _overlay_reference_layers(fig, rodb, svg_rot, theta, first_lyr, cfg)
    if active_layer is None:
        # No reference layers — draw the unit cell outline and apply layout.
        fig.add_shape(type="rect", x0=0, y0=0, x1=disp_w, y1=disp_h,
                      line=dict(color="rgba(0,180,80,0.5)", width=1.5),
                      fillcolor="rgba(0,0,0,0)", layer="below")
        _apply_layout(fig, cfg)

    _add_grid(fig, disp_w, disp_h)
    _panel_lbl = ", ".join(panel_filter) if panel_filter else None
    _add_dim_annotations(fig, disp_w, disp_h, active_layer, panel_label=_panel_lbl)

    _show_heatmap = st.toggle("🌡️ Density Heatmap", value=False,
                              help="Overlay a 2D defect density heatmap instead of individual dots",
                              key="cm_heatmap_toggle")
    if _show_heatmap and len(cm_plot) >= 3:
        _add_density_heatmap(fig, cm_plot, disp_w, disp_h)

    n_def, n_units = len(cm_plot), len(sel_units)
    fig.update_layout(title=dict(
        text=f"{n_def} defects · {n_units} units · avg {n_def/max(n_units,1):.1f}/unit",
        font=dict(color="rgba(180,180,180,0.8)", size=12, family="monospace"),
        x=0.5, xanchor="center",
    ))

    _export_col, _ = st.columns([1, 4])
    with _export_col:
        try:
            _png = export_current_view(fig, fmt='png', scale=3)
            st.download_button("📷 Export PNG", data=_png, file_name="commonality_unit.png",
                               mime="image/png", width="stretch")
        except Exception:
            st.button("📷 Export PNG (kaleido required)", disabled=True, width="stretch")

    st.plotly_chart(fig, width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})

    _render_metrics(fp_df, sel_units, cm_plot)
    _fault_site_table(fp_df)


def _add_grid(fig, cell_w, cell_h, step=5.0):
    _g = step
    while _g < cell_w:
        fig.add_shape(type="line", x0=_g, y0=0, x1=_g, y1=cell_h,
                      line=dict(color="rgba(255,255,255,0.06)", width=1), layer="below")
        _g += step
    _g = step
    while _g < cell_h:
        fig.add_shape(type="line", x0=0, y0=_g, x1=cell_w, y1=_g,
                      line=dict(color="rgba(255,255,255,0.06)", width=1), layer="below")
        _g += step


def _add_density_heatmap(fig, cm_plot, cell_w, cell_h):
    try:
        _hx = cm_plot['ALIGNED_X'].dropna().values
        _hy = cm_plot['ALIGNED_Y'].dropna().values
        _nx, _ny = 60, 60
        _gx = np.linspace(0, cell_w, _nx)
        _gy = np.linspace(0, cell_h, _ny)
        _z, _, _ = np.histogram2d(_hy, _hx, bins=[_ny, _nx],
                                  range=[[0, cell_h], [0, cell_w]])
        from scipy.ndimage import gaussian_filter as _gf
        _z = _gf(_z.astype(float), sigma=2.0)
        fig.add_trace(go.Heatmap(z=_z, x=_gx, y=_gy, colorscale='Hot',
                                 opacity=0.55, showscale=False, hoverinfo='skip'))
    except Exception:
        st.warning("Heatmap requires scipy. Install with: pip install scipy")


def _render_empty_layers(rodb, first_lyr, cell_w, cell_h):
    """Show the reference design (no defects) when the scope filter matched nothing.
    Rotated to panel orientation, consistent with the defect/empty states."""
    if not (rodb and rodb.layers and first_lyr):
        return
    checked = [(n, l) for n, l in rodb.layers.items() if st.session_state.get(f"vis_{n}", False)]
    if not checked:
        st.caption("☝️ Select a layer in the sidebar to view the design.")
        return
    _ref_shift = (-first_lyr.bounds[0], -first_lyr.bounds[1])
    is_multi = len(checked) > 1
    _rot = float(round(getattr(rodb.panel_layout, 'dominant_angle', 0.0)) % 360) if rodb.panel_layout else 0.0
    _swap = _rot in (90.0, 270.0)
    if _swap:
        cell_w, cell_h = cell_h, cell_w
    fig = go.Figure()
    for _idx, (_n, _l) in enumerate(sorted(checked, key=_layer_sort_key)):
        _lc = _MULTI_LAYER_COLORS[_idx % len(_MULTI_LAYER_COLORS)] if is_multi else None
        _place_layer_image(fig, _n, _l, _ref_shift, _rot, _swap, is_multi, layer_color=_lc)
    _add_dim_annotations(fig, cell_w, cell_h, " + ".join(n for n, _ in checked))
    fig.update_layout(
        xaxis=dict(range=[-1, cell_w + 1], scaleanchor='y', scaleratio=1,
                   showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(range=[-1, cell_h + 1], showgrid=False, zeroline=False, showticklabels=False),
        plot_bgcolor='#000000', paper_bgcolor='#000000', font=dict(color='#cccccc'),
        margin=dict(l=0, r=0, t=36, b=0), height=800,
    )
    st.plotly_chart(fig, width='stretch',
                    config={'scrollZoom': True, 'displayModeBar': True, 'displaylogo': False})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@st.fragment
def render_unit_commonality(parsed, aoi, align_args, get_svg_url):
    st.markdown("### 🗺️ Commonality — Defect Superposition")
    st.caption("Normalise each selected unit's defects into local coordinates and overlay on a single reference unit.")

    rodb = st.session_state.get('rendered_odb')
    has_aoi_cm = (
        aoi and aoi.has_data
        and 'UNIT_INDEX_X' in aoi.all_defects.columns
        and 'UNIT_INDEX_Y' in aoi.all_defects.columns
    )

    if not rodb and not has_aoi_cm:
        st.info("Upload a TGZ design file or AOI defect data to use this view.")
        return

    na_checked = _render_sidebar_controls(rodb)

    if not has_aoi_cm:
        _render_empty_state(rodb, na_checked)
    else:
        _render_defect_state(rodb, aoi, align_args)
