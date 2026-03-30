import streamlit as st
import pandas as pd


def render_panelization_data(parsed, aoi, align_args):
    st.markdown("### 📊 Panelization Data")
    st.caption("Structural layout derived from ODB++ step-repeat hierarchy — useful for root-cause analysis.")

    _rodb = st.session_state.get('rendered_odb')

    if not _rodb or not _rodb.panel_layout:
        st.info("Load a TGZ file to see panelization data.")
        return

    pl = _rodb.panel_layout
    uw, uh = pl.unit_bounds
    positions = pl.unit_positions  # list of (x_mm, y_mm) — panel display coords

    # ── 1. High-level summary ─────────────────────────────────────────────────
    st.markdown("#### Panel Summary")
    _c1, _c2, _c3, _c4, _c5 = st.columns(5)
    _c1.metric("Panel Width",  f"{pl.panel_width:.2f} mm")
    _c2.metric("Panel Height", f"{pl.panel_height:.2f} mm")
    _c3.metric("Unit Width (profile)", f"{uw:.3f} mm",
               help="From ODB++ board profile — the physical board edge. Used for centering and alignment.")
    _c4.metric("Unit Height (profile)", f"{uh:.3f} mm",
               help="From ODB++ board profile — the physical board edge. Used for centering and alignment.")
    _c5.metric("Total Units",  pl.total_units)

    _c6, _c7, _c8 = st.columns(3)
    _c6.metric("Rows", pl.rows)
    _c7.metric("Cols", pl.cols)

    # Copper bounds: first non-drill layer vs aggregate
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
            f"Copper (1st layer / all layers)",
            f"{_cop_w:.2f}×{_cop_h:.2f} / {_agg_w:.2f}×{_agg_h:.2f} mm",
            help=(
                "Left value: bounds of the first non-drill copper layer.\n"
                "Right value: aggregate of ALL copper layers (widest reach, includes rails).\n"
                "Neither is the board size — use Profile above for that."
            )
        )

    st.divider()

    # ── 2. Step-repeat hierarchy ──────────────────────────────────────────────
    st.markdown("#### Step-Repeat Hierarchy")
    _hier = pl.step_hierarchy
    if _hier:
        _rows_h = []
        for _parent, _repeats in _hier.items():
            for _sr in _repeats:
                _rows_h.append({
                    'Parent Step':  _parent,
                    'Child Step':   _sr.child_step,
                    'Origin X (mm)': round(_sr.x, 4),
                    'Origin Y (mm)': round(_sr.y, 4),
                    'Repeat X (nx)': _sr.nx,
                    'Repeat Y (ny)': _sr.ny,
                    'Pitch X (mm)':  round(_sr.dx, 4),
                    'Pitch Y (mm)':  round(_sr.dy, 4),
                })
        _df_h = pd.DataFrame(_rows_h)
        st.dataframe(_df_h, use_container_width=True, hide_index=True)

        # ── Derived gaps from hierarchy ───────────────────────────────────────
        st.markdown("#### Derived Gaps")
        _gap_rows = []
        for _sr_row in _rows_h:
            _px = _sr_row['Pitch X (mm)']
            _py = _sr_row['Pitch Y (mm)']
            _nx = _sr_row['Repeat X (nx)']
            _ny = _sr_row['Repeat Y (ny)']
            if _nx > 1 and _px > 0:
                _gap_rows.append({
                    'Level':    f"{_sr_row['Parent Step']} → {_sr_row['Child Step']}",
                    'Axis':     'X',
                    'Pitch (mm)': _px,
                    'Unit size used (mm)': uw,
                    'Gap (mm)': round(_px - uw, 4),
                    'Repeats':  _nx,
                })
            if _ny > 1 and _py > 0:
                _gap_rows.append({
                    'Level':    f"{_sr_row['Parent Step']} → {_sr_row['Child Step']}",
                    'Axis':     'Y',
                    'Pitch (mm)': _py,
                    'Unit size used (mm)': uh,
                    'Gap (mm)': round(_py - uh, 4),
                    'Repeats':  _ny,
                })
        if _gap_rows:
            st.dataframe(pd.DataFrame(_gap_rows), use_container_width=True, hide_index=True)
            st.caption(
                "Gap = Pitch − Unit size. "
                "At the unit level this is the inter-unit spacing. "
                "At higher levels (cluster, panel) it is the spacing between groups."
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
                # Check this position actually exists (some panels aren't full grids)
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
                        'X right edge (mm)': round(_ux + uw, 3),
                        'Y top edge (mm)':   round(_uy + uh, 3),
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
                _sample['In range X?'] = _sample['X_MM - unit_origin_X'].between(0, uw)
                _sample['In range Y?'] = _sample['Y_MM - unit_origin_Y'].between(0, uh)
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
                    f"Expected range: X=[{_ux_sel:.1f}, {_ux_sel+uw:.1f}], Y=[{_uy_sel:.1f}, {_uy_sel+uh:.1f}]"
                )
