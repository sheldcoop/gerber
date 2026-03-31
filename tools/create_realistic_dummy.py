"""
create_realistic_dummy.py
Generates 20 realistic dummy AOI Excel files based on the real coordinate
distribution from the actual Ouroboros AOI report. Each panel file will have
defects clustered around the same hotspots but with natural per-panel variation.
"""
import pandas as pd
import numpy as np
import os

SOURCE = '/Users/prince/gerber/BU-02B_report_2026-02-19T13_36_24.297595_1-1.xlsx'
OUT_DIR = '/Users/prince/gerber/dummy_data'
NUM_PANELS = 20

def generate_realistic_heatmap_data() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    # ── Load the real Defects sheet ─────────────────────────────────────────
    real_df = pd.read_excel(SOURCE, sheet_name='Defects', engine='openpyxl')
    print(f"Loaded {len(real_df)} real defects from source file.")
    print(f"X range (mm): {real_df['X_COORDINATES'].min()/1000:.1f} – {real_df['X_COORDINATES'].max()/1000:.1f}")
    print(f"Y range (mm): {real_df['Y_COORDINATES'].min()/1000:.1f} – {real_df['Y_COORDINATES'].max()/1000:.1f}")

    x_vals = real_df['X_COORDINATES'].dropna().values
    y_vals = real_df['Y_COORDINATES'].dropna().values
    defect_types = real_df['DEFECT_TYPE'].dropna().tolist()

    # Compute natural per-cluster spread from real data quartiles
    x_iqr = np.percentile(x_vals, 75) - np.percentile(x_vals, 25)
    y_iqr = np.percentile(y_vals, 75) - np.percentile(y_vals, 25)
    spread_x = max(x_iqr * 0.1, 2000)  # min 2mm spread in microns
    spread_y = max(y_iqr * 0.1, 2000)

    # Pick 5 hotspot seeds from the real data (evenly spread across the distribution)
    indices = np.linspace(0, len(x_vals) - 1, 5, dtype=int)
    hotspots = [(x_vals[i], y_vals[i], spread_x, spread_y, 45) for i in indices]
    print(f"\n{len(hotspots)} hotspot anchors selected from real data.")

    # ── Generate 20 panels ───────────────────────────────────────────────────
    for panel_i in range(NUM_PANELS):
        rows = []

        # Clustered defects around real hotspots
        for anchor_x, anchor_y, sx, sy, count in hotspots:
            xs = np.random.normal(anchor_x, sx, count)
            ys = np.random.normal(anchor_y, sy, count)
            for x, y in zip(xs, ys):
                rows.append({
                    'DEFECT_ID':    int(np.random.randint(1000, 9999)),
                    'DEFECT_TYPE':  np.random.choice(defect_types),
                    'X_COORDINATES': float(x),
                    'Y_COORDINATES': float(y),
                    'UNIT_INDEX_X': int(np.random.randint(0, 6)),
                    'UNIT_INDEX_Y': int(np.random.randint(0, 6)),
                    'VERIFICATION': np.random.choice(
                        ['F', 'GE22', 'CU14', 'CU18', 'CU10', 'GE57', 'CU22'],
                        p=[0.39, 0.21, 0.15, 0.13, 0.06, 0.03, 0.03],
                    ),
                })

        # Random scatter defects to simulate dust across the full panel
        full_xs = np.random.uniform(x_vals.min(), x_vals.max(), 20)
        full_ys = np.random.uniform(y_vals.min(), y_vals.max(), 20)
        for x, y in zip(full_xs, full_ys):
            rows.append({
                'DEFECT_ID':    int(np.random.randint(1000, 9999)),
                'DEFECT_TYPE':  'Island',
                'X_COORDINATES': float(x),
                'Y_COORDINATES': float(y),
                'UNIT_INDEX_X': 0,
                'UNIT_INDEX_Y': 0,
                'VERIFICATION': 'F',
            })

        panel_df = pd.DataFrame(rows)
        out_path = os.path.join(OUT_DIR, f"Realistic_Panel_{panel_i}_BU-02B.xlsx")
        panel_df.to_excel(out_path, index=False)
        print(f"  Generated {out_path} ({len(panel_df)} rows)")

    print("\nDone! All 20 panels generated.")

if __name__ == "__main__":
    generate_realistic_heatmap_data()
