import streamlit as st
import pandas as pd
from collections import defaultdict


def render_panelization_data(parsed, aoi, align_args) -> None:
    st.markdown("### 📊 Panelization Data")
    st.caption("Structural layout derived from ODB++ step-repeat hierarchy — useful for root-cause analysis.")

    _rodb = st.session_state.get('rendered_odb')

    if not _rodb or not _rodb.panel_layout:
        st.info("Load a TGZ file to see panelization data.")
        return

    pl = _rodb.panel_layout
    uw, uh = pl.unit_bounds
    positions = pl.unit_positions  # list of (x_mm, y_mm) — panel display coords

    # For rotated units (90°/270°), panel X footprint = local Y, panel Y footprint = local X
    _dom = getattr(pl, 'dominant_angle', 0.0)
    if _dom in (90.0, 270.0):
        cell_w, cell_h = uh, uw   # panel X footprint, panel Y footprint
    else:
        cell_w, cell_h = uw, uh

    # ── 1. High-level summary ─────────────────────────────────────────────────
    st.markdown("#### Panel Summary")
    _c1, _c2, _c3, _c4, _c5 = st.columns(5)
    _c1.metric("Panel Width",  f"{pl.panel_width:.2f} mm")
    _c2.metric("Panel Height", f"{pl.panel_height:.2f} mm")
    _c3.metric("Unit Width (panel X)",  f"{cell_w:.3f} mm",
               help="Physical unit width in panel space. For rotated units (90°/270°) this is the local Y dimension.")
    _c4.metric("Unit Height (panel Y)", f"{cell_h:.3f} mm",
               help="Physical unit height in panel space. For rotated units (90°/270°) this is the local X dimension.")
    _c5.metric("Total Units", pl.total_units)

    _c6, _c7, _c8 = st.columns(3)
    _c6.metric("Rows", pl.rows)
    _c7.metric("Cols", pl.cols)
    if _dom:
        _c8.metric("Placement Angle", f"{_dom:.0f}°",
                   help="Dominant ANGLE from the cluster STEP-REPEAT blocks.")

    # Copper bounds for reference
    _first_lyr = next(
        (l for l in _rodb.layers.values() if l.layer_type != 'drill'),
        next(iter(_rodb.layers.values()), None)
    )
    if _first_lyr:
        _cb = _first_lyr.bounds
        _cop_w = _cb[2] - _cb[0]
        _cop_h = _cb[3] - _cb[1]
        _bb = _rodb.board_bounds
        _agg_w = _bb[2] - _bb[0]
        _agg_h = _bb[3] - _bb[1]
        _c8.metric(
            "Copper (1st / all layers)",
            f"{_cop_w:.2f}×{_cop_h:.2f} / {_agg_w:.2f}×{_agg_h:.2f} mm",
            help=(
                "Left: bounds of the first non-drill copper layer.\n"
                "Right: aggregate of ALL copper layers.\n"
                "Use Profile above for board outline dimensions."
            )
        ) if not _dom else None

    st.divider()

    # ── 2. Step-repeat hierarchy ──────────────────────────────────────────────
    st.markdown("#### Step-Repeat Hierarchy")
    _hier = pl.step_hierarchy
    if _hier:
        # Collect all raw rows first
        _rows_h = []
        for _parent, _repeats in _hier.items():
            for _sr in _repeats:
                _rows_h.append({
                    'Parent Step':   _parent,
                    'Child Step':    _sr.child_step,
                    'Origin X (mm)': round(_sr.x, 4),
                    'Origin Y (mm)': round(_sr.y, 4),
                    'Repeat X (nx)': _sr.nx,
                    'Repeat Y (ny)': _sr.ny,
                    'Pitch X (mm)':  round(_sr.dx, 4),
                    'Pitch Y (mm)':  round(_sr.dy, 4),
                    'Angle (°)':     int(_sr.angle),
                })

        # Consolidate multiple NX=1/NY=1 individual-placement entries (written
        # by InCAM Pro for ANGLE≠0) into one summary row per (parent, child)
        # pair — so you always see exactly one row per hierarchy level.
        _groups_display = defaultdict(list)
        for _r in _rows_h:
            _groups_display[(_r['Parent Step'], _r['Child Step'])].append(_r)

        _rows_consolidated = []
        for (_parent, _child), _entries in _groups_display.items():
            if len(_entries) == 1:
                _rows_consolidated.append(_entries[0])
            else:
                _xs = sorted(set(round(_r['Origin X (mm)'], 4) for _r in _entries))
                _ys = sorted(set(round(_r['Origin Y (mm)'], 4) for _r in _entries))
                _nx = len(_xs)
                _ny = len(_ys)
                _angles = [_r['Angle (°)'] for _r in _entries]
                _rows_consolidated.append({
                    'Parent Step':   _parent,
                    'Child Step':    _child,
                    'Origin X (mm)': min(_xs),
                    'Origin Y (mm)': min(_ys),
                    'Repeat X (nx)': _nx,
                    'Repeat Y (ny)': _ny,
                    'Pitch X (mm)':  round((_xs[-1] - _xs[0]) / (_nx - 1), 4) if _nx > 1 else 0,
                    'Pitch Y (mm)':  round((_ys[-1] - _ys[0]) / (_ny - 1), 4) if _ny > 1 else 0,
                    'Angle (°)':     max(set(_angles), key=_angles.count),
                })

        _df_h = pd.DataFrame(_rows_consolidated)
        st.dataframe(_df_h, use_container_width=True, hide_index=True)

        # ── Derived gaps ───────────────────────────────────────────────────────
        # Handles both encoding styles:
        #   Compact grid:   1 entry with NX>1 and DX>0  (ANGLE=0 designs)
        #   Individual:     N entries each NX=1 DX=0    (ANGLE=270 designs)
        st.markdown("#### Derived Gaps")
        _groups = defaultdict(list)
        for _r in _rows_h:
            _groups[(_r['Parent Step'], _r['Child Step'])].append(_r)

        _gap_rows = []
        for (_parent, _child), _entries in _groups.items():
            _level = f"{_parent} → {_child}"
            # Dominant angle for this group → swap unit dims if 90°/270°
            _dom_angle = max(
                set(_r['Angle (°)'] for _r in _entries),
                key=[_r['Angle (°)'] for _r in _entries].count
            )
            _eff_uw = uh if _dom_angle in (90, 270) else uw  # effective X footprint
            _eff_uh = uw if _dom_angle in (90, 270) else uh  # effective Y footprint

            if len(_entries) == 1:
                # Compact grid (NX/NY > 1 with explicit DX/DY)
                _r = _entries[0]
                if _r['Repeat X (nx)'] > 1 and _r['Pitch X (mm)'] > 0:
                    _gap_rows.append({
                        'Level': _level, 'Axis': 'X',
                        'Pitch (mm)': _r['Pitch X (mm)'],
                        'Unit size used (mm)': round(_eff_uw, 4),
                        'Gap (mm)': round(_r['Pitch X (mm)'] - _eff_uw, 4),
                        'Repeats': _r['Repeat X (nx)'],
                    })
                if _r['Repeat Y (ny)'] > 1 and _r['Pitch Y (mm)'] > 0:
                    _gap_rows.append({
                        'Level': _level, 'Axis': 'Y',
                        'Pitch (mm)': _r['Pitch Y (mm)'],
                        'Unit size used (mm)': round(_eff_uh, 4),
                        'Gap (mm)': round(_r['Pitch Y (mm)'] - _eff_uh, 4),
                        'Repeats': _r['Repeat Y (ny)'],
                    })
            else:
                # Individual-placement style (NX=1, DX=0 per entry)
                # Derive pitch from sorted position differences
                _xs = sorted(set(round(_r['Origin X (mm)'], 3) for _r in _entries))
                _ys = sorted(set(round(_r['Origin Y (mm)'], 3) for _r in _entries))
                if len(_xs) > 1:
                    _derived_dx = (_xs[-1] - _xs[0]) / (len(_xs) - 1)
                    _gap_rows.append({
                        'Level': _level, 'Axis': 'X',
                        'Pitch (mm)': round(_derived_dx, 4),
                        'Unit size used (mm)': round(_eff_uw, 4),
                        'Gap (mm)': round(_derived_dx - _eff_uw, 4),
                        'Repeats': len(_xs),
                    })
                if len(_ys) > 1:
                    _derived_dy = (_ys[-1] - _ys[0]) / (len(_ys) - 1)
                    _gap_rows.append({
                        'Level': _level, 'Axis': 'Y',
                        'Pitch (mm)': round(_derived_dy, 4),
                        'Unit size used (mm)': round(_eff_uh, 4),
                        'Gap (mm)': round(_derived_dy - _eff_uh, 4),
                        'Repeats': len(_ys),
                    })

        if _gap_rows:
            st.dataframe(pd.DataFrame(_gap_rows), use_container_width=True, hide_index=True)
            st.caption(
                "Gap = Pitch − Unit size. "
                "At the unit level this is the inter-unit spacing. "
                "At higher levels (cluster, panel) it is the spacing between groups. "
                "Pitch is derived from position differences when DX/DY=0 (individual placements)."
            )
        else:
            st.info("Single unit — no repeats to compute gaps from.")
    else:
        st.info("No step-repeat hierarchy found in ODB++.")

    st.divider()

    # ── 3. Per-unit coordinates table ─────────────────────────────────────────
    st.markdown("#### Unit Coordinates  (panel display space, lower-left origin)")
    st.caption(
        "These are the panel-absolute X/Y positions (mm) of each unit's copper left/bottom edge — "
        "the same coordinate system as your AOI machine. "
        "Use these to verify: defect X_MM − Unit X should give a coordinate within [0, unit_width]."
    )

    if positions:
        _uniq_x = sorted(set(round(x, 3) for x, _ in positions))
        _uniq_y = sorted(set(round(y, 3) for _, y in positions))

        _unit_rows = []
        for _iy, _uy in enumerate(_uniq_y):
            for _ix, _ux in enumerate(_uniq_x):
                _exists = any(
                    abs(px - _ux) < 0.1 and abs(py - _uy) < 0.1
                    for px, py in positions
                )
                if _exists:
                    _unit_rows.append({
                        'Unit Index (Row, Col)': f"({_iy}, {_ix})",
                        'Row': _iy,
                        'Col': _ix,
                        'X mm (left edge)':  round(_ux, 3),
                        'Y mm (bottom edge)': round(_uy, 3),
                        'X right edge (mm)': round(_ux + cell_w, 3),
                        'Y top edge (mm)':   round(_uy + cell_h, 3),
                    })

        _df_units = pd.DataFrame(_unit_rows)
        st.dataframe(
            _df_units,
            use_container_width=True,
            hide_index=True,
            column_config={
                'X mm (left edge)':   st.column_config.NumberColumn(format='%.3f'),
                'Y mm (bottom edge)': st.column_config.NumberColumn(format='%.3f'),
                'X right edge (mm)':  st.column_config.NumberColumn(format='%.3f'),
                'Y top edge (mm)':    st.column_config.NumberColumn(format='%.3f'),
            }
        )

        # ── Quick verification helper ─────────────────────────────────────────
        if aoi and aoi.has_data and 'UNIT_INDEX_Y' in aoi.all_defects.columns:
            st.divider()
            st.markdown("#### AOI ↔ Unit Coordinate Verification")
            st.caption(
                "Pick a unit to check that defect coordinates land inside it. "
                "A defect should satisfy:  0 ≤ (X_MM − unit_X) ≤ unit_width  and similarly for Y."
            )
            _sel_row = st.selectbox("Unit Row (UNIT_INDEX_Y)", options=sorted(aoi.all_defects['UNIT_INDEX_Y'].unique().astype(int)))
            _sel_col = st.selectbox("Unit Col (UNIT_INDEX_X)", options=sorted(aoi.all_defects['UNIT_INDEX_X'].unique().astype(int)))

            _mask = (
                (aoi.all_defects['UNIT_INDEX_Y'].astype(int) == _sel_row) &
                (aoi.all_defects['UNIT_INDEX_X'].astype(int) == _sel_col)
            )
            _sample = aoi.all_defects[_mask][['X_MM', 'Y_MM']].head(10).copy()

            if not _sample.empty and _sel_row < len(_uniq_y) and _sel_col < len(_uniq_x):
                _ux_sel = _uniq_x[_sel_col]
                _uy_sel = _uniq_y[_sel_row]
                _sample['X_MM - unit_origin_X'] = (_sample['X_MM'] - _ux_sel).round(3)
                _sample['Y_MM - unit_origin_Y'] = (_sample['Y_MM'] - _uy_sel).round(3)
                _sample['In range X?'] = _sample['X_MM - unit_origin_X'].between(0, cell_w)
                _sample['In range Y?'] = _sample['Y_MM - unit_origin_Y'].between(0, cell_h)
                _sample = _sample.round(3)

                _all_ok_x = _sample['In range X?'].all()
                _all_ok_y = _sample['In range Y?'].all()

                if _all_ok_x and _all_ok_y:
                    st.success(f"✅ Unit ({_sel_row},{_sel_col}) — all sample defects land inside unit bounds. Alignment looks correct.")
                else:
                    st.error(
                        f"⚠️ Unit ({_sel_row},{_sel_col}) — some defects are OUTSIDE unit bounds. "
                        f"X OK: {_all_ok_x}, Y OK: {_all_ok_y}. Check unit_positions centering or AOI coordinate system."
                    )

                st.dataframe(
                    _sample[['X_MM', 'Y_MM', 'X_MM - unit_origin_X', 'Y_MM - unit_origin_Y', 'In range X?', 'In range Y?']],
                    use_container_width=True, hide_index=True
                )
                st.caption(
                    f"Unit ({_sel_row},{_sel_col}) position: X={_ux_sel:.3f} mm, Y={_uy_sel:.3f} mm. "
                    f"Expected range: X=[{_ux_sel:.1f}, {_ux_sel+cell_w:.1f}], Y=[{_uy_sel:.1f}, {_uy_sel+cell_h:.1f}]"
                )
