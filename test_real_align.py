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
    
    sh = _parse_step_repeat(job_root, 25.4)
    pl = compute_unit_positions(sh, (33.5, 33.5))
    
    df = pd.read_excel('dummy_data/BU-01/BU-01B_Panel_01.xlsx')
    df['X_MM'] = df['X_COORDINATES'] / 1000.0
    
    origins, cw, ch = compute_cm_geometry(pl.unit_positions, (0,0,33.5,33.5), (33.5, 33.5))
    
    pairs = list(zip(df['UNIT_INDEX_Y'].astype(int), df['UNIT_INDEX_X'].astype(int)))
    ox = [origins.get(p, (0.0, 0.0))[0] for p in pairs]
    
    df['OX'] = ox
    df['ALIGNED_X'] = df['X_MM'] - df['OX']
    
    print(df[['X_MM', 'OX', 'ALIGNED_X', 'UNIT_INDEX_X']].head(10))
    print("ALIGNED_X min/max:", df['ALIGNED_X'].min(), df['ALIGNED_X'].max())
finally:
    shutil.rmtree(temp_dir)
