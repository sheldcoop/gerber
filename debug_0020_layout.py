import sys
import os
import tarfile
import tempfile
import shutil

sys.path.insert(0, os.path.abspath('.'))
from core.pipeline import _parse_step_repeat
from core.step_layout import compute_unit_positions
from core.data_utils import compute_cm_geometry

temp_dir = tempfile.mkdtemp()
try:
    with tarfile.open('fhr0020_bkm.tgz', 'r:gz') as tar:
        tar.extractall(temp_dir)
    job_root = [os.path.join(temp_dir, d) for d in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, d)) and d != '__MACOSX'][0]
    
    sh = _parse_step_repeat(job_root, 25.4)
    pl = compute_unit_positions(sh, (43.5, 37.5))
    
    origins, cw, ch = compute_cm_geometry(pl.unit_positions, (0,0,43.5,37.5), (43.5, 37.5))
    
    print(f"Number of units: {len(pl.unit_positions)}")
    print(f"Max ri: {max(k[0] for k in origins.keys())}")
    print(f"Max ci: {max(k[1] for k in origins.keys())}")
finally:
    shutil.rmtree(temp_dir)
