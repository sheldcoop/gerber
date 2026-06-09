"""
views/cm_geometry.py — unit selection + reference-cell origin geometry for the
Unit Commonality view. Split out of unit_commonality.py to keep the view thin.
"""

import streamlit as st

from core.data_utils import compute_cm_geometry
from alignment import calculate_geometry, INTER_UNIT_GAP


def _select_units(rodb, aoi):
    """Render the unit multiselect + Q1-Q4 buttons.

    Returns (sel_units, all_pairs, q_rows, q_cols, gap_x, gap_y).
    """
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
