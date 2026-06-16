import pandas as pd
import numpy as np
from alignment import calculate_physical_unit_origin, calculate_geometry

df = pd.read_excel('BU-02B_report_2026-02-19T13_36_24.297595_1-1.xlsx', sheet_name='Defects')
df['X_MM'] = df['X_COORDINATES'] / 1000.0
df['Y_MM'] = df['Y_COORDINATES'] / 1000.0

print(f'Rows: {len(df)}')
print(f'X_MM range: {df.X_MM.min():.3f} to {df.X_MM.max():.3f}')
print(f'Y_MM range: {df.Y_MM.min():.3f} to {df.Y_MM.max():.3f}')
print(f'UNIT_INDEX_X unique: {sorted(df.UNIT_INDEX_X.unique().astype(int).tolist())}')
print(f'UNIT_INDEX_Y unique: {sorted(df.UNIT_INDEX_Y.unique().astype(int).tolist())}')
print()

# --- Infer actual stride from data ---
# For each unique col/row, find median X_MM / Y_MM of defects
x_meds = {int(c): df[df.UNIT_INDEX_X == c].X_MM.median() for c in df.UNIT_INDEX_X.unique()}
y_meds = {int(r): df[df.UNIT_INDEX_Y == r].Y_MM.median() for r in df.UNIT_INDEX_Y.unique()}
x_sorted = sorted(x_meds.items())
y_sorted = sorted(y_meds.items())
x_gaps = [x_sorted[i+1][1] - x_sorted[i][1] for i in range(len(x_sorted)-1)]
y_gaps = [y_sorted[i+1][1] - y_sorted[i][1] for i in range(len(y_sorted)-1)]
print('X gaps between consecutive col medians:', [f'{g:.2f}' for g in x_gaps])
print('Y gaps between consecutive row medians:', [f'{g:.2f}' for g in y_gaps])
print()

ctx = calculate_geometry(6, 6, 5.0, 3.5)
print(f'Cell (default 6x6, dyn=5.0x3.5): {ctx.cell_width:.3f} x {ctx.cell_height:.3f} mm')
print(f'Stride: {ctx.stride_x:.3f} x {ctx.stride_y:.3f} mm')
print(f'effective_gap_x={ctx.effective_gap_x:.2f}  effective_gap_y={ctx.effective_gap_y:.2f}')
print()

print('Outside units (tolerance=1mm):')
count = 0
for _, row in df.iterrows():
    uy, ux = int(row['UNIT_INDEX_Y']), int(row['UNIT_INDEX_X'])
    ox, oy = calculate_physical_unit_origin(uy, ux, 6, 6, 5.0, 3.5)
    lx = row['X_MM'] - ox
    ly = row['Y_MM'] - oy
    if lx < -1 or lx > ctx.cell_width+1 or ly < -1 or ly > ctx.cell_height+1:
        print(f'  unit(row={uy},col={ux})  origin=({ox:.2f},{oy:.2f})  abs=({row.X_MM:.2f},{row.Y_MM:.2f})  local=({lx:.2f},{ly:.2f})')
        count += 1
print(f'Total outside: {count}/{len(df)}')
print()

print('Code-computed X origins per col:')
for col in range(12):
    ox, _ = calculate_physical_unit_origin(0, col, 6, 6, 5.0, 3.5)
    print(f'  col={col:2d}  origin_x={ox:.2f}mm  right={ox+ctx.cell_width:.2f}mm   data_median={x_meds.get(col, "n/a")}')

print()
print('Code-computed Y origins per row:')
for r in range(12):
    _, oy = calculate_physical_unit_origin(r, 0, 6, 6, 5.0, 3.5)
    print(f'  row={r:2d}  origin_y={oy:.2f}mm  top={oy+ctx.cell_height:.2f}mm   data_median={y_meds.get(r, "n/a")}')

