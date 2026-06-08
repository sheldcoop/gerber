import sys
import os
import tarfile
import tempfile
import shutil
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath('.'))
from core.pipeline import _parse_step_repeat
from core.step_layout import compute_unit_positions

def generate_inspired_excel(tgz_file, out_file, w, h):
    temp_dir = tempfile.mkdtemp()
    try:
        with tarfile.open(tgz_file, 'r:gz') as tar:
            tar.extractall(temp_dir)
        job_root = [os.path.join(temp_dir, d) for d in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, d)) and d != '__MACOSX'][0]
        
        sh = _parse_step_repeat(job_root, 25.4)
        pl = compute_unit_positions(sh, (w, h))
        
        uniq_x = sorted(list(set([round(p[0], 2) for p in pl.unit_positions])))
        uniq_y = sorted(list(set([round(p[1], 2) for p in pl.unit_positions])))
        
        data = []
        defect_types = ['Line Nick', 'Island', 'Short', 'Nick', 'Open', 'Pinhole', 'Protrusion', 'Splash']
        verifications = ['N', 'Y', 'FP', 'SH', 'OP', 'CU22', 'CU10', 'GE22']
        
        for p in pl.unit_positions:
            px, py = p
            col = uniq_x.index(round(px, 2))
            row = uniq_y.index(round(py, 2))
            
            # The real AOI machine clusters defects near specific structural points (pads).
            # We simulate this by creating 2-3 "hotspots" per unit, and adding a few defects around each.
            for _ in range(np.random.randint(1, 4)):
                hotspot_x = px + np.random.uniform(2, w - 2)
                hotspot_y = py + np.random.uniform(2, h - 2)
                
                # Add 1 to 5 defects around this hotspot
                for _ in range(np.random.randint(1, 6)):
                    defect_x = hotspot_x + np.random.uniform(-0.5, 0.5)
                    defect_y = hotspot_y + np.random.uniform(-0.5, 0.5)
                    
                    data.append({
                        'DEFECT_ID': int(np.random.randint(1000, 9999)),
                        'DEFECT_TYPE': np.random.choice(defect_types),
                        'X_COORDINATES': defect_x * 1000.0,
                        'Y_COORDINATES': defect_y * 1000.0,
                        'UNIT_INDEX_X': col,
                        'UNIT_INDEX_Y': row,
                        'VERIFICATION': np.random.choice(verifications)
                    })
        
        df = pd.DataFrame(data)
        df.to_excel(out_file, index=False)
        print(f"Generated {out_file} with {len(df)} rows.")
    finally:
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    generate_inspired_excel('fhr0010_bkm.tgz', 'test_fhr0010.xlsx', 33.5, 33.5)
    generate_inspired_excel('fhr0020_bkm.tgz', 'test_fhr0020.xlsx', 43.5, 37.5)
