import sys
import os
import tarfile
import tempfile
import shutil
import pandas as pd

sys.path.insert(0, os.path.abspath('.'))
from core.pipeline import _parse_step_repeat, _read_features_text, _parse_features_text
from core.step_layout import compute_unit_positions
from core.data_utils import compute_cm_geometry

temp_dir = tempfile.mkdtemp()
try:
    with tarfile.open('fhr0020_bkm.tgz', 'r:gz') as tar:
        tar.extractall(temp_dir)
    job_root = [os.path.join(temp_dir, d) for d in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, d)) and d != '__MACOSX'][0]
    
    sh = _parse_step_repeat(job_root, 25.4)
    pl = compute_unit_positions(sh, (43.5, 37.5))
    
    # We will test with Panel1_BU-02B.xlsx or any FHR0020 dummy data if they have it
    # But wait, what file does the user use for FHR0020?
    
    origins, cw, ch = compute_cm_geometry(pl.unit_positions, (0,0,43.5,37.5), (43.5, 37.5))
    
    print("FHR0020 Origins keys:", list(origins.keys())[:5])
    print("FHR0020 First position:", pl.unit_positions[0])
finally:
    shutil.rmtree(temp_dir)
