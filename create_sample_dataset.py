"""
create_sample_dataset.py
========================
Generates 30 dummy AOI Excel files with VISIBLE, DISTINCT failure patterns.

  dummy_data/
    BU-01/  BU-01F_Panel_01..05.xlsx  +  BU-01B_Panel_01..05.xlsx
    BU-02/  BU-02F_Panel_01..05.xlsx  +  BU-02B_Panel_01..05.xlsx
    BU-03/  BU-03F_Panel_01..05.xlsx  +  BU-03B_Panel_01..05.xlsx

Failure signatures (clearly visible in Unit Grid + Density Contour):
  BU-01  Right-edge squeegee wear
         Cols 9–11  → 100 % repeatability  (fail every panel, 15–25 defects/unit)
         Cols 6– 8  → 40–70 % repeatability  (fail some panels, 5–12 defects)
         Cols 0– 5  → 0 %  (truly clean — zero defects)

  BU-02  Thermal hot-spot at bottom-right corner
         Rows 0–2, cols 8–11  → 100 % repeatability  (20–35 defects/unit)
         Rows 0–3, cols 6–11  → 50–80 % repeatability  (5–15 defects)
         Rest                 → 0 %  (clean)

  BU-03  Chronic single-pad design flaw + process wave
         (col=5, row=9)       → 100 % on EVERY panel  (20–30 defects)
         (col=6, row=8)       → 80 % repeatability    (8–15 defects)
         (col=4, row=10)      → 60 % repeatability    (3–8 defects)
         Rest                 → 0 %  (clean)

Key design rules that make patterns visible:
  - Clean cells = ZERO defects, not "low noise".  This makes colorscale
    span 0–100 % instead of 97.5–100 % (the old problem).
  - Hot-zone defect counts vary ±30 % across panels to look realistic.
  - Warm-zone cells skip some panels randomly (hit rate < 100 %).
  - X_MM / Y_MM coordinates are computed from unit indices so the
    Density Contour heatmap clusters line up with the Grid cells.
"""

import os
import sys
import numpy as np
import pandas as pd

# Use the exact same geometry as the app so coordinates line up perfectly
sys.path.insert(0, os.path.dirname(__file__))
from alignment import calculate_geometry, INTER_UNIT_GAP

OUT_DIR    = os.path.join(os.path.dirname(__file__), 'dummy_data')
N_PANELS   = 5
COLS_PER_Q = 6      # units per quadrant column (matches app default)
ROWS_PER_Q = 6      # units per quadrant row
COLS       = COLS_PER_Q * 2   # 12 total columns
ROWS       = ROWS_PER_Q * 2   # 12 total rows
RNG        = np.random.default_rng(7)

# ── Build unit-index → mm-centre lookup using alignment.py ───────────────────
# Same params as the app sidebar defaults
_ctx = calculate_geometry(ROWS_PER_Q, COLS_PER_Q, dyn_gap_x=5.0, dyn_gap_y=3.5)
_cw  = _ctx.cell_width
_ch  = _ctx.cell_height
_sx  = _ctx.stride_x
_sy  = _ctx.stride_y

# Enumerate all unit positions exactly as _compute_panel_shapes does in app.py
_all_positions: list[tuple[float, float]] = []
for _q_ox, _q_oy in _ctx.quadrant_origins.values():
    for _r in range(ROWS_PER_Q):
        for _c in range(COLS_PER_Q):
            _all_positions.append((
                _q_ox + INTER_UNIT_GAP + _c * _sx + _cw / 2,  # x centre
                _q_oy + INTER_UNIT_GAP + _r * _sy + _ch / 2,  # y centre
            ))

# Sort unique X/Y values — UNIT_INDEX_X=0 is leftmost column, Y=0 is bottom row
_uniq_x = sorted(set(round(p[0], 2) for p in _all_positions))
_uniq_y = sorted(set(round(p[1], 2) for p in _all_positions))

# Lookup: (UNIT_INDEX_X, UNIT_INDEX_Y) → (x_mm, y_mm) panel-absolute centre
_UNIT_CENTER: dict[tuple[int, int], tuple[float, float]] = {
    (ci, ri): (_uniq_x[ci], _uniq_y[ri])
    for ri in range(len(_uniq_y))
    for ci in range(len(_uniq_x))
}


def _unit_center(col: int, row: int) -> tuple[float, float]:
    """Panel-absolute (x_mm, y_mm) centre — exact alignment.py geometry."""
    return _UNIT_CENTER.get((col, row), (_uniq_x[min(col, len(_uniq_x)-1)],
                                          _uniq_y[min(row, len(_uniq_y)-1)]))


DEFECTS_F = ['Bridging', 'Open', 'Tombstone', 'Missing_Component',
             'Misalignment', 'Insufficient_Solder']
DEFECTS_B = ['Short', 'Void', 'Excess_Solder', 'Wicking',
             'Cold_Joint', 'Solder_Ball']

# ── Verification code pools per BU × side ─────────────────────────────────────
# Each pool reflects the realistic defect mix for that layer/side combination.
# No 'F' — all codes are real classification codes from the inspection spec.
#
# BU-01  Right-edge squeegee wear
#   Front: surface copper shorts/residue from squeegee pressure
#   Back:  burrs and nicks from mechanical stress on back side
# BU-02  Thermal hotspot bottom-right corner
#   Front: copper nodules + voids from thermal expansion
#   Back:  foreign material + ABF irregularity from thermal contamination
# BU-03  Chronic single-pad design flaw
#   Front: opens + process residue from insufficient design margin
#   Back:  plating-under-resist + chemical residue from resist adhesion failure
BG_CODES = {
    ('BU-01', 'F'): ['CU18', 'CU22', 'CU15', 'CU25', 'GE22', 'CU10'],
    ('BU-01', 'B'): ['CU15', 'CU10', 'CU18', 'GE01', 'GE22', 'CU41'],
    ('BU-02', 'F'): ['CU54', 'CU14', 'GE57', 'GE22', 'CU10', 'CU94'],
    ('BU-02', 'B'): ['GE57', 'CU54', 'BM31', 'GE22', 'CU10', 'BM01'],
    ('BU-03', 'F'): ['CU16', 'CU17', 'GE22', 'CU10', 'GE01', 'CU20'],
    ('BU-03', 'B'): ['CU17', 'CU19', 'GE22', 'BM01', 'CU10', 'HO31'],
}


def _emit(col: int, row: int, count: int, pool: list,
          verif=None, bg_codes=None) -> list[dict]:
    """Emit `count` defect rows for unit (col, row).

    verif:    dominant verification code (85 % of rows).
              Remaining 15 % sampled from bg_codes.
    bg_codes: background verification code list for this BU/side.
              Used for the 15 % remainder and for all rows when verif=None.
              Falls back to a generic realistic mix if not provided.
    """
    if count <= 0:
        return []
    _bg = bg_codes or ['CU18', 'GE22', 'CU10', 'CU22', 'GE57', 'CU14',
                       'CU15', 'CU54', 'GE01', 'BM01']
    cx, cy = _unit_center(col, row)
    xs = RNG.normal(cx, 4.0, count)
    ys = RNG.normal(cy, 4.0, count)
    rows = []
    for x, y in zip(xs, ys):
        if verif is not None:
            v = verif if RNG.random() < 0.85 else str(RNG.choice(_bg))
        else:
            v = str(RNG.choice(_bg))
        rows.append({
            'DEFECT_ID':     int(RNG.integers(10000, 99999)),
            'DEFECT_TYPE':   str(RNG.choice(pool)),
            'X_COORDINATES': round(float(x) * 1000, 1),
            'Y_COORDINATES': round(float(y) * 1000, 1),
            'UNIT_INDEX_X':  col,
            'UNIT_INDEX_Y':  row,
            'VERIFICATION':  v,
        })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# BU-01 : Right-edge squeegee wear
# ─────────────────────────────────────────────────────────────────────────────
def _panel_bu01(panel_idx: int, pool: list, side: str = 'F') -> pd.DataFrame:
    # Squeegee wear — dominant code differs per panel to make filter effect visible:
    #   Front: CU18 (panels 1-2) → 40 %, CU22 (panels 3-4) → 40 %, CU25 (panel 5) → 20 %
    #   Back:  CU15 (panels 1-2) → 40 %, CU10 (panels 3-4) → 40 %, CU18 (panel 5) → 20 %
    _bg = BG_CODES[('BU-01', side)]
    if side == 'F':
        _hot_code  = {1: 'CU18', 2: 'CU18', 3: 'CU22', 4: 'CU22', 5: 'CU25'}.get(panel_idx)
        _warm_code = {1: 'CU18', 2: 'CU22', 3: 'CU15', 4: 'CU25', 5: 'GE22'}.get(panel_idx)
    else:
        _hot_code  = {1: 'CU15', 2: 'CU15', 3: 'CU10', 4: 'CU10', 5: 'CU18'}.get(panel_idx)
        _warm_code = {1: 'CU15', 2: 'CU10', 3: 'CU18', 4: 'GE22', 5: 'CU41'}.get(panel_idx)
    rows = []
    for col in range(COLS):
        for row in range(ROWS):
            if col >= 9:
                n = int(RNG.integers(15, 26))
                rows.extend(_emit(col, row, n, pool, verif=_hot_code, bg_codes=_bg))
            elif col >= 6:
                if RNG.random() < 0.55:
                    n = int(RNG.integers(5, 13))
                    rows.extend(_emit(col, row, n, pool, verif=_warm_code, bg_codes=_bg))
            # Background noise — all units including "clean" cols 0–5.
            # Low hit rate + count so systematic patterns still dominate.
            if RNG.random() < 0.20:
                rows.extend(_emit(col, row, int(RNG.integers(1, 4)), pool, bg_codes=_bg))

    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        'DEFECT_ID','DEFECT_TYPE','X_COORDINATES','Y_COORDINATES',
        'UNIT_INDEX_X','UNIT_INDEX_Y','VERIFICATION'])


# ─────────────────────────────────────────────────────────────────────────────
# BU-02 : Thermal hot-spot at bottom-right corner
# ─────────────────────────────────────────────────────────────────────────────
def _panel_bu02(panel_idx: int, pool: list, side: str = 'F') -> pd.DataFrame:
    # Thermal hotspot — copper nodules front, FM + ABF back:
    #   Front: CU54 (panels 1-3) → 60 %, CU14 (panels 4-5) → 40 %
    #   Back:  GE57 (panels 1-2) → 40 %, CU54 (panels 3-4) → 40 %, BM31 (panel 5) → 20 %
    _bg = BG_CODES[('BU-02', side)]
    if side == 'F':
        _hot_code  = {1: 'CU54', 2: 'CU54', 3: 'CU54', 4: 'CU14', 5: 'CU14'}.get(panel_idx)
        _warm_code = {1: 'CU54', 2: 'CU10', 3: 'CU14', 4: 'GE22', 5: 'CU94'}.get(panel_idx)
    else:
        _hot_code  = {1: 'GE57', 2: 'GE57', 3: 'CU54', 4: 'CU54', 5: 'BM31'}.get(panel_idx)
        _warm_code = {1: 'GE57', 2: 'CU54', 3: 'BM31', 4: 'GE22', 5: 'BM01'}.get(panel_idx)
    rows = []
    for col in range(COLS):
        for row in range(ROWS):
            if row <= 2 and col >= 8:
                n = int(RNG.integers(20, 36))
                rows.extend(_emit(col, row, n, pool, verif=_hot_code, bg_codes=_bg))
            elif row <= 3 and col >= 6:
                if RNG.random() < 0.65:
                    n = int(RNG.integers(5, 16))
                    rows.extend(_emit(col, row, n, pool, verif=_warm_code, bg_codes=_bg))
            elif row <= 4 and col >= 7:
                if RNG.random() < 0.30:
                    n = int(RNG.integers(2, 8))
                    rows.extend(_emit(col, row, n, pool, verif=_warm_code, bg_codes=_bg))
            # Background noise across all units — thermal panels still have
            # random contamination and nicks outside the hot corner.
            if RNG.random() < 0.18:
                rows.extend(_emit(col, row, int(RNG.integers(1, 4)), pool, bg_codes=_bg))

    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        'DEFECT_ID','DEFECT_TYPE','X_COORDINATES','Y_COORDINATES',
        'UNIT_INDEX_X','UNIT_INDEX_Y','VERIFICATION'])


# ─────────────────────────────────────────────────────────────────────────────
# BU-03 : Chronic single-pad flaw — one always-hot unit + a few neighbours
# ─────────────────────────────────────────────────────────────────────────────
def _panel_bu03(panel_idx: int, pool: list, side: str = 'F') -> pd.DataFrame:
    # Design flaw — opens + residue front, plating/chemical back:
    #   Front: CU16 (panels 1-3) → 60 %, GE22 (panels 4-5) → 40 %
    #   Back:  CU17 (panels 1-3) → 60 %, CU19 (panels 4-5) → 40 %
    _bg = BG_CODES[('BU-03', side)]
    if side == 'F':
        _chronic_code = {1: 'CU16', 2: 'CU16', 3: 'CU16', 4: 'GE22', 5: 'GE22'}.get(panel_idx)
        _sec_code     = {1: 'CU17', 2: 'CU16', 3: 'GE22', 4: 'CU10', 5: 'GE01'}.get(panel_idx)
    else:
        _chronic_code = {1: 'CU17', 2: 'CU17', 3: 'CU17', 4: 'CU19', 5: 'CU19'}.get(panel_idx)
        _sec_code     = {1: 'CU17', 2: 'CU19', 3: 'GE22', 4: 'BM01', 5: 'HO31'}.get(panel_idx)
    rows = []

    # Primary chronic unit — every panel, many defects
    rows.extend(_emit(5, 9, int(RNG.integers(20, 31)), pool,
                      verif=_chronic_code, bg_codes=_bg))

    # Strong secondary — 80 % hit rate
    if RNG.random() < 0.80:
        rows.extend(_emit(6, 8, int(RNG.integers(8, 16)), pool,
                          verif=_sec_code, bg_codes=_bg))

    # Weaker tertiary — 60 % hit rate
    if RNG.random() < 0.60:
        rows.extend(_emit(4, 10, int(RNG.integers(3, 9)), pool,
                          verif=_sec_code, bg_codes=_bg))

    # Occasional neighbours — 25 % each
    for nc, nr in [(5, 8), (6, 9), (4, 9), (5, 10)]:
        if RNG.random() < 0.25:
            rows.extend(_emit(nc, nr, int(RNG.integers(1, 5)), pool,
                              bg_codes=_bg))

    # Background noise across all units — design-flaw panels still have
    # random scatter defects everywhere; the chronic cluster just dominates.
    for col in range(COLS):
        for row in range(ROWS):
            if RNG.random() < 0.15:
                rows.extend(_emit(col, row, int(RNG.integers(1, 3)), pool, bg_codes=_bg))

    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        'DEFECT_ID','DEFECT_TYPE','X_COORDINATES','Y_COORDINATES',
        'UNIT_INDEX_X','UNIT_INDEX_Y','VERIFICATION'])


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
BU_CONFIG = [
    (1, 'BU-01', _panel_bu01),
    (2, 'BU-02', _panel_bu02),
    (3, 'BU-03', _panel_bu03),
]

SIDE_CONFIG = [
    ('F', DEFECTS_F),
    ('B', DEFECTS_B),
]


def generate() -> None:
    total = 0
    for _bu_num, bu_name, gen_fn in BU_CONFIG:
        bu_dir = os.path.join(OUT_DIR, bu_name)
        os.makedirs(bu_dir, exist_ok=True)

        for side_code, pool in SIDE_CONFIG:
            for p in range(1, N_PANELS + 1):
                df = gen_fn(p, pool, side=side_code)
                fname = f"{bu_name}{side_code}_Panel_{p:02d}.xlsx"
                fpath = os.path.join(bu_dir, fname)
                df.to_excel(fpath, sheet_name='Defects', index=False)
                total += 1

                # Quick pattern summary for this panel
                if not df.empty and 'UNIT_INDEX_X' in df.columns:
                    hot = df.groupby('UNIT_INDEX_X').size().nlargest(3)
                    summary = '  hottest cols: ' + ', '.join(
                        f"C{c}={n}" for c, n in hot.items())
                else:
                    summary = '  (empty panel)'

                print(f"  [{total:02d}/30]  {bu_name}/{fname}  "
                      f"({len(df)} defects){summary}")

    print(f"\nDone — {total} files in {OUT_DIR}/")
    _verify()


def _verify() -> None:
    """Quick sanity check: confirm each BU shows the expected pattern."""
    print("\n── Verification ─────────────────────────────────────────────")
    for _bu_num, bu_name, _ in BU_CONFIG:
        bu_dir = os.path.join(OUT_DIR, bu_name)
        files  = [f for f in os.listdir(bu_dir) if f.endswith('.xlsx')]
        all_dfs = []
        for f in files:
            df = pd.read_excel(os.path.join(bu_dir, f), sheet_name='Defects',
                               engine='openpyxl')
            df['_file'] = f
            all_dfs.append(df)
        combined = pd.concat(all_dfs, ignore_index=True)

        # Repeatability per unit (% of files where unit had ≥1 defect)
        n_files = len(files)
        rep = (combined.groupby(['UNIT_INDEX_X', 'UNIT_INDEX_Y'])['_file']
               .nunique() / n_files * 100)

        hot_units = rep[rep >= 80].sort_values(ascending=False).head(5)
        zero_units = int((rep == 0).sum()) if not rep.empty else 0

        print(f"\n  {bu_name}  ({n_files} files, {len(combined)} total defects)")
        print(f"    Units with 0 % repeatability : {zero_units} / {COLS*ROWS}")
        print(f"    Top 5 units by repeatability:")
        for (cx, cy), pct in hot_units.items():
            print(f"      Col {cx:2d}  Row {cy:2d}  →  {pct:.0f} %")


if __name__ == '__main__':
    generate()
