import pandas as pd

df = pd.read_excel('BU-02B.xlsx', sheet_name='Defects')
print('Rows:', len(df))
print('X_COORDINATES: min=%d max=%d' % (df['X_COORDINATES'].min(), df['X_COORDINATES'].max()))
print('Y_COORDINATES: min=%d max=%d' % (df['Y_COORDINATES'].min(), df['Y_COORDINATES'].max()))
print('X_MM range: %.3f to %.3f' % (df['X_COORDINATES'].min()/1000, df['X_COORDINATES'].max()/1000))
print('Y_MM range: %.3f to %.3f' % (df['Y_COORDINATES'].min()/1000, df['Y_COORDINATES'].max()/1000))
print()
print('UNIT_INDEX_X unique:', sorted(df['UNIT_INDEX_X'].unique().tolist()))
print('UNIT_INDEX_Y unique:', sorted(df['UNIT_INDEX_Y'].unique().tolist()))
print()

# Per-unit X/Y ranges to understand the coordinate system
print('--- Per-unit X/Y ranges (microns) ---')
for uy in sorted(df['UNIT_INDEX_Y'].unique())[:3]:
    for ux in sorted(df['UNIT_INDEX_X'].unique())[:3]:
        g = df[(df['UNIT_INDEX_X']==ux) & (df['UNIT_INDEX_Y']==uy)]
        if len(g) > 0:
            print(f'  Unit({uy},{ux}): N={len(g)}  X=[{g["X_COORDINATES"].min()},{g["X_COORDINATES"].max()}]  Y=[{g["Y_COORDINATES"].min()},{g["Y_COORDINATES"].max()}]')

print()
# Compute expected unit origins using panel math
from alignment import calculate_physical_unit_origin, calculate_geometry
ctx = calculate_geometry(panel_rows=6, panel_cols=6, dyn_gap_x=5.0, dyn_gap_y=3.5)
print(f'Cell size (default 6x6): {ctx.cell_width:.3f} x {ctx.cell_height:.3f} mm')
print(f'Stride: {ctx.stride_x:.3f} x {ctx.stride_y:.3f} mm')
print()

for uy in sorted(df['UNIT_INDEX_Y'].unique())[:3]:
    for ux in sorted(df['UNIT_INDEX_X'].unique())[:3]:
        g = df[(df['UNIT_INDEX_X']==ux) & (df['UNIT_INDEX_Y']==uy)]
        if len(g) == 0:
            continue
        ox, oy = calculate_physical_unit_origin(uy, ux, 6, 6, 5.0, 3.5)
        local_x = g['X_COORDINATES'].values / 1000.0 - ox
        local_y = g['Y_COORDINATES'].values / 1000.0 - oy
        print(f'  Unit({uy},{ux}): origin=({ox:.2f},{oy:.2f})  local_X=[{local_x.min():.2f},{local_x.max():.2f}]  local_Y=[{local_y.min():.2f},{local_y.max():.2f}]')
        outside = ((local_x < 0) | (local_x > ctx.cell_width) | (local_y < 0) | (local_y > ctx.cell_height)).sum()
        print(f'    -> {outside}/{len(g)} defects OUTSIDE unit boundary')
