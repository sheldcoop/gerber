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
    with tarfile.open('fhr0010_bkm.tgz', 'r:gz') as tar:
        tar.extractall(temp_dir)
    job_root = [os.path.join(temp_dir, d) for d in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, d)) and d != '__MACOSX'][0]
    
    prof_path = os.path.join(job_root, 'steps', 'unit', 'profile')
    geoms, _, _, _, _, _ = _parse_features_text(_read_features_text(prof_path), 1.0, set())
    unit_w = max(g.bounds[2] for g in geoms) - min(g.bounds[0] for g in geoms)
    # the parser handles scaling, so we just pass unit_w=33.5 from 25.4 internally
    
    sh = _parse_step_repeat(job_root, 25.4)
    pl = compute_unit_positions(sh, (33.5, 33.5))
    
    df = pd.read_excel('test_data_fhr0010.xlsx')
    
    origins, cw, ch = compute_cm_geometry(pl.unit_positions, (0,0,33.5,33.5), (33.5, 33.5))
    
    pairs = list(zip(df['UNIT_INDEX_Y'].astype(int), df['UNIT_INDEX_X'].astype(int)))
    ox = [origins.get(p, (0.0, 0.0))[0] for p in pairs]
    
    fails = sum(1 for o in ox if o == 0.0)
    print(f"Total pairs: {len(pairs)}")
    print(f"Origins keys: {len(origins)} keys")
    print(f"Failed lookups: {fails}")
    
    if fails > 0:
        print("First few pairs:", pairs[:5])
        print("First few keys in origins:", list(origins.keys())[:5])
finally:
    shutil.rmtree(temp_dir)
