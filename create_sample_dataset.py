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


def _emit(col: int, row: int, count: int, pool: list) -> list[dict]:
    """Emit `count` defect rows for unit (col, row)."""
    if count <= 0:
        return []
    cx, cy = _unit_center(col, row)
    # Scatter within ±4 mm of cell centre — stays well inside 35×36 mm cell
    xs = RNG.normal(cx, 4.0, count)
    ys = RNG.normal(cy, 4.0, count)
    return [{
        'DEFECT_ID':     int(RNG.integers(10000, 99999)),
        'DEFECT_TYPE':   str(RNG.choice(pool)),
        'X_COORDINATES': round(float(x) * 1000, 1),   # microns
        'Y_COORDINATES': round(float(y) * 1000, 1),   # microns
        'UNIT_INDEX_X':  col,
        'UNIT_INDEX_Y':  row,
        'VERIFICATION':  str(RNG.choice(['N', 'Y', 'FP'], p=[0.70, 0.20, 0.10])),
    } for x, y in zip(xs, ys)]


# ─────────────────────────────────────────────────────────────────────────────
# BU-01 : Right-edge squeegee wear
# ─────────────────────────────────────────────────────────────────────────────
def _panel_bu01(panel_idx: int, pool: list) -> pd.DataFrame:
    rows = []
    for col in range(COLS):
        for row in range(ROWS):
            if col >= 9:
                # Hot: always fails, count varies per panel
                n = int(RNG.integers(15, 26))
                rows.extend(_emit(col, row, n, pool))

            elif col >= 6:
                # Warm: fails on ~50 % of panels
                if RNG.random() < 0.55:
                    n = int(RNG.integers(5, 13))
                    rows.extend(_emit(col, row, n, pool))
                # else: zero defects this panel for this cell

            # Cols 0–5: completely clean — no defects added

    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        'DEFECT_ID','DEFECT_TYPE','X_COORDINATES','Y_COORDINATES',
        'UNIT_INDEX_X','UNIT_INDEX_Y','VERIFICATION'])


# ─────────────────────────────────────────────────────────────────────────────
# BU-02 : Thermal hot-spot at bottom-right corner
# ─────────────────────────────────────────────────────────────────────────────
def _panel_bu02(panel_idx: int, pool: list) -> pd.DataFrame:
    rows = []
    for col in range(COLS):
        for row in range(ROWS):
            if row <= 2 and col >= 8:
                # Hot corner: always fails
                n = int(RNG.integers(20, 36))
                rows.extend(_emit(col, row, n, pool))

            elif row <= 3 and col >= 6:
                # Warm zone: fails ~65 % of panels
                if RNG.random() < 0.65:
                    n = int(RNG.integers(5, 16))
                    rows.extend(_emit(col, row, n, pool))

            elif row <= 4 and col >= 7:
                # Outer warm ring: fails ~30 % of panels
                if RNG.random() < 0.30:
                    n = int(RNG.integers(2, 8))
                    rows.extend(_emit(col, row, n, pool))

            # Rest: clean

    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        'DEFECT_ID','DEFECT_TYPE','X_COORDINATES','Y_COORDINATES',
        'UNIT_INDEX_X','UNIT_INDEX_Y','VERIFICATION'])


# ─────────────────────────────────────────────────────────────────────────────
# BU-03 : Chronic single-pad flaw — one always-hot unit + a few neighbours
# ─────────────────────────────────────────────────────────────────────────────
def _panel_bu03(panel_idx: int, pool: list) -> pd.DataFrame:
    rows = []

    # Primary chronic unit — every panel, many defects
    rows.extend(_emit(5, 9, int(RNG.integers(20, 31)), pool))

    # Strong secondary — 80 % hit rate
    if RNG.random() < 0.80:
        rows.extend(_emit(6, 8, int(RNG.integers(8, 16)), pool))

    # Weaker tertiary — 60 % hit rate
    if RNG.random() < 0.60:
        rows.extend(_emit(4, 10, int(RNG.integers(3, 9)), pool))

    # Occasional neighbours — 25 % each
    for nc, nr in [(5, 8), (6, 9), (4, 9), (5, 10)]:
        if RNG.random() < 0.25:
            rows.extend(_emit(nc, nr, int(RNG.integers(1, 5)), pool))

    # Rest of panel: truly clean

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
                df = gen_fn(p, pool)
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
