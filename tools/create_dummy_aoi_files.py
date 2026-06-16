"""
create_dummy_aoi_files.py
Generates clean dummy AOI Excel files based on the real BU-02B AOI report.
  - 20 x BU-02B panels  → Panel1_BU-02B.xlsx ... Panel20_BU-02B.xlsx
  - 20 x BU-03F panels  → Panel1_BU-03F.xlsx ... Panel20_BU-03F.xlsx
Coordinates are sampled from real data so they cover the full board.
"""
import pandas as pd
import numpy as np
import os

SOURCE  = '/Users/prince/gerber/BU-02B_report_2026-02-19T13_36_24.297595_1-1.xlsx'
OUT_DIR = '/Users/prince/gerber/dummy_data'
NUM_PANELS = 20

def _load_real() -> pd.DataFrame:
    df = pd.read_excel(SOURCE, sheet_name='Defects', engine='openpyxl')
    print(f"Loaded {len(df)} real defects | X: {df['X_COORDINATES'].min()/1000:.0f}–{df['X_COORDINATES'].max()/1000:.0f}mm | Y: {df['Y_COORDINATES'].min()/1000:.0f}–{df['Y_COORDINATES'].max()/1000:.0f}mm")
    return df

def _make_panel(real_df: pd.DataFrame, hotspots: list, spread: float) -> pd.DataFrame:
    rows = []
    defect_types = real_df['DEFECT_TYPE'].dropna().tolist()

    # Only generate defects at real pad anchor points — no random scatter.
    # This matches real AOI machine behaviour (only inspects copper pads).
    for anchor_x, anchor_y in hotspots:
        xs = np.random.normal(anchor_x, spread, 50)
        ys = np.random.normal(anchor_y, spread, 50)
        for x, y in zip(xs, ys):
            rows.append({
                'DEFECT_ID':     int(np.random.randint(1000, 9999)),
                'DEFECT_TYPE':   np.random.choice(defect_types),
                'X_COORDINATES': float(x),
                'Y_COORDINATES': float(y),
                'UNIT_INDEX_X':  int(np.random.randint(0, 6)),
                'UNIT_INDEX_Y':  int(np.random.randint(0, 6)),
                'VERIFICATION':  np.random.choice(
                    ['F', 'GE22', 'CU14', 'CU18', 'CU10', 'GE57', 'CU22'],
                    p=[0.39, 0.21, 0.15, 0.13, 0.06, 0.03, 0.03],
                ),
            })

    return pd.DataFrame(rows)

def generate_all() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    real_df = _load_real()

    x_vals = real_df['X_COORDINATES'].dropna().values
    y_vals = real_df['Y_COORDINATES'].dropna().values

    # 5 anchor hotspots evenly sampled across the real distribution
    idx = np.linspace(0, len(x_vals) - 1, 5, dtype=int)
    hotspots_b = [(x_vals[i], y_vals[i]) for i in idx]

    # BU-03F gets slightly shifted hotspots to simulate a different layer
    hotspots_f = [(x + 15000, y + 10000) for x, y in hotspots_b]

    # Fixed tight spread: 1000 microns = 1mm — defects stay within one unit cell pad area
    spread = 1000.0

    print("\nGenerating BU-02B panels...")
    for i in range(1, NUM_PANELS + 1):
        df = _make_panel(real_df, hotspots_b, spread)
        path = os.path.join(OUT_DIR, f"Panel{i}_BU-02B.xlsx")
        df.to_excel(path, index=False)
        print(f"  {path}  ({len(df)} rows)")

    print("\nGenerating BU-03F panels...")
    for i in range(1, NUM_PANELS + 1):
        df = _make_panel(real_df, hotspots_f, spread)
        path = os.path.join(OUT_DIR, f"Panel{i}_BU-03F.xlsx")
        df.to_excel(path, index=False)
        print(f"  {path}  ({len(df)} rows)")

    print(f"\nDone! {NUM_PANELS*2} total files in {OUT_DIR}/")

if __name__ == "__main__":
    generate_all()
