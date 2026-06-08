import os
import sys
import pandas as pd
import tarfile
import tempfile
import shutil

sys.path.insert(0, os.path.abspath('.'))
from core.pipeline import _parse_step_repeat, _read_features_text, _parse_features_text
from core.step_layout import compute_unit_positions

# Simulate the UI auto-shift logic
def get_auto_shift(min_x, min_y, max_x, max_y, rot_deg):
    rot_norm = round(rot_deg) % 360
    if rot_norm == 90:
        new_min_x, new_min_y = -max_y, min_x
    elif rot_norm == 180:
        new_min_x, new_min_y = -max_x, -max_y
    elif rot_norm == 270:
        new_min_x, new_min_y = min_y, -max_x
    else:
        new_min_x, new_min_y = min_x, min_y
    return -new_min_x, -new_min_y

def _align_defects(x_mm, y_mm, ox_arr, oy_arr, off_x, off_y, unit_angle=0.0):
    import numpy as _np
    ax = _np.array(x_mm) - _np.array(ox_arr) + off_x
    ay = _np.array(y_mm) - _np.array(oy_arr) + off_y
    return list(ax), list(ay)

for tgz, excel, unit_w, unit_h, is_270 in [
    ('fhr0010_bkm.tgz', 'test_data_fhr0010.xlsx', 33.5, 33.5, False),
    ('fhr0020_bkm.tgz', 'test_data_fhr0020.xlsx', 43.5, 37.5, True)
]:
    if not os.path.exists(tgz) or not os.path.exists(excel):
        print(f"Skipping {tgz}")
        continue
        
    print(f"\n--- Testing {tgz} ---")
    df = pd.read_excel(excel)
    
    # Extract bounding box to compute auto_shift
    temp_dir = tempfile.mkdtemp()
    try:
        with tarfile.open(tgz, 'r:gz') as tar:
            tar.extractall(temp_dir)
            
        job_root = [os.path.join(temp_dir, d) for d in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, d)) and d != '__MACOSX'][0]
        
        prof_path = os.path.join(job_root, 'steps', 'unit', 'profile')
        text = _read_features_text(prof_path)
        geoms, _, _, _, _, _ = _parse_features_text(text, 1.0, set())
        min_x = min(g.bounds[0] for g in geoms)
        max_x = max(g.bounds[2] for g in geoms)
        min_y = min(g.bounds[1] for g in geoms)
        max_y = max(g.bounds[3] for g in geoms)
        
        sh = _parse_step_repeat(job_root, 1.0)
        # Apply inch quirks as we did during generation
        all_s = [sr.dx for v in sh.values() for sr in v if sr.dx > 0]
        if all_s and min(all_s) < 5.0 and min(all_s)*25.4 > 20:
            sh = _parse_step_repeat(job_root, 25.4)
        else:
            sr_max = max([max(abs(sr.x), abs(sr.y)) for v in sh.values() for sr in v] + [0])
            if sr_max > 0 and sr_max < unit_w:
                sh = _parse_step_repeat(job_root, 25.4)
                
        pl = compute_unit_positions(sh, (unit_w, unit_h))
        unit_positions = pl.unit_positions
        dominant_angle = getattr(pl, 'dominant_angle', 0.0)
        
        uniq_x = sorted(list(set([round(p[0], 2) for p in unit_positions])))
        uniq_y = sorted(list(set([round(p[1], 2) for p in unit_positions])))
        origins = {(ri, ci): (uniq_x[ci], uniq_y[ri]) for ri in range(len(uniq_y)) for ci in range(len(uniq_x))}
        
        print(f"Dominant Angle: {dominant_angle}")
        print(f"Original Unit Bounding Box: {min_x:.2f} to {max_x:.2f}, {min_y:.2f} to {max_y:.2f}")
        
        auto_shift_x, auto_shift_y = get_auto_shift(min_x, min_y, max_x, max_y, dominant_angle)
        print(f"Calculated Auto Shift: X={auto_shift_x:.2f}, Y={auto_shift_y:.2f}")
        
        ox_arr = [origins.get((row, col), (0,0))[0] for row, col in zip(df['UNIT_INDEX_Y'], df['UNIT_INDEX_X'])]
        oy_arr = [origins.get((row, col), (0,0))[1] for row, col in zip(df['UNIT_INDEX_Y'], df['UNIT_INDEX_X'])]
        
        ax, ay = _align_defects(df['X_COORDINATES'].tolist(), df['Y_COORDINATES'].tolist(), ox_arr, oy_arr, auto_shift_x, auto_shift_y, dominant_angle)
        
        canvas_w = unit_h if dominant_angle in (90, 270) else unit_w
        canvas_h = unit_w if dominant_angle in (90, 270) else unit_h
        print(f"Plotly Canvas Size: {canvas_w} x {canvas_h}")
        
        min_ax, max_ax = min(ax), max(ax)
        min_ay, max_ay = min(ay), max(ay)
        
        print(f"Resulting Aligned Defects Bounds:")
        print(f"X: {min_ax:.2f} to {max_ax:.2f}")
        print(f"Y: {min_ay:.2f} to {max_ay:.2f}")
        
        if min_ax >= -0.1 and max_ax <= canvas_w + 0.1 and min_ay >= -0.1 and max_ay <= canvas_h + 0.1:
            print("✅ SUCCESS! All defects fall perfectly inside the Plotly Canvas!")
        else:
            print("❌ FAILURE! Defects exceed canvas bounds!")
            
    finally:
        shutil.rmtree(temp_dir)
