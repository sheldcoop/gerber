import os
import pandas as pd
import numpy as np

def make_excel(name, bounds_w, bounds_h, positions):
    data = []
    # positions is a list of (x, y) origins
    uniq_x = sorted(list(set([round(p[0], 2) for p in positions])))
    uniq_y = sorted(list(set([round(p[1], 2) for p in positions])))
    
    for px, py in positions:
        col = uniq_x.index(round(px, 2))
        row = uniq_y.index(round(py, 2))
        
        # Center cluster
        for _ in range(np.random.randint(2, 5)):
            data.append({
                'DEFECT_TYPE': 'Short',
                'X_COORDINATES': px + np.random.uniform(-5, 5),
                'Y_COORDINATES': py + np.random.uniform(-5, 5),
                'VERIFICATION': 'SH',
                'UNIT_INDEX_X': col,
                'UNIT_INDEX_Y': row,
            })
            
        # Top right cluster
        if np.random.rand() > 0.5:
            for _ in range(np.random.randint(1, 4)):
                data.append({
                    'DEFECT_TYPE': 'Open',
                    'X_COORDINATES': px + bounds_w/2 - np.random.uniform(2, 6),
                    'Y_COORDINATES': py + bounds_h/2 - np.random.uniform(2, 6),
                    'VERIFICATION': 'OP',
                    'UNIT_INDEX_X': col,
                    'UNIT_INDEX_Y': row,
                })
                
        # Noise
        for _ in range(np.random.randint(0, 3)):
            data.append({
                'DEFECT_TYPE': np.random.choice(['Short', 'Open', 'Pin Hole']),
                'X_COORDINATES': px + np.random.uniform(-bounds_w/2, bounds_w/2),
                'Y_COORDINATES': py + np.random.uniform(-bounds_h/2, bounds_h/2),
                'VERIFICATION': np.random.choice(['CU22', 'SH', 'OP']),
                'UNIT_INDEX_X': col,
                'UNIT_INDEX_Y': row,
            })
            
    df = pd.DataFrame(data)
    df.to_excel(name, index=False)
    print(f"Created {name} with {len(df)} defects.")

# FHR0010 Data
# Run pipeline to get unit positions
import sys
sys.path.insert(0, os.path.abspath('.'))
from core.pipeline import _parse_step_repeat
from core.step_layout import compute_unit_positions
import tarfile
import tempfile
import shutil

for tgz, out_name, unit_w, unit_h, is_270 in [
    ('fhr0010_bkm.tgz', 'test_data_fhr0010.xlsx', 33.5, 33.5, False),
    ('fhr0020_bkm.tgz', 'test_data_fhr0020.xlsx', 43.5, 37.5, True)
]:
    if not os.path.exists(tgz): continue
    
    temp_dir = tempfile.mkdtemp()
    try:
        with tarfile.open(tgz, 'r:gz') as tar:
            tar.extractall(temp_dir)
            
        job_root = None
        for item in os.listdir(temp_dir):
            if os.path.isdir(os.path.join(temp_dir, item)) and item != '__MACOSX':
                job_root = os.path.join(temp_dir, item)
                break
        
        sh = _parse_step_repeat(job_root, 1.0)
        # Handle inch quirk
        all_s = []
        for v in sh.values():
            for sr in v:
                if sr.dx > 0: all_s.append(sr.dx)
        if all_s and min(all_s) < 5.0 and min(all_s)*25.4 > 20:
            sh = _parse_step_repeat(job_root, 25.4)
        else:
            sr_max = max([max(abs(sr.x), abs(sr.y)) for v in sh.values() for sr in v] + [0])
            if sr_max > 0 and sr_max < unit_w:
                sh = _parse_step_repeat(job_root, 25.4)
                
        pl = compute_unit_positions(sh, (unit_w, unit_h))
        
        # for 270, the physical bounding box is swapped
        bounds_w = unit_h if is_270 else unit_w
        bounds_h = unit_w if is_270 else unit_h
        
        make_excel(out_name, bounds_w, bounds_h, pl.unit_positions)
    finally:
        shutil.rmtree(temp_dir)

