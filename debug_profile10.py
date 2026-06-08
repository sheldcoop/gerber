import os
import tarfile
import tempfile
import shutil
import sys

tgz_path = 'fhr0010_bkm.tgz'
temp_dir = tempfile.mkdtemp()
try:
    with tarfile.open(tgz_path, 'r:gz') as tar:
        tar.extractall(temp_dir)
        
    job_root = None
    for item in os.listdir(temp_dir):
        if os.path.isdir(os.path.join(temp_dir, item)) and item != '__MACOSX':
            job_root = os.path.join(temp_dir, item)
            break
    if not job_root:
        job_root = temp_dir
        
    sys.path.insert(0, os.path.abspath('.'))
    from core.pipeline import _read_features_text, _parse_features_text
    
    prof_path = os.path.join(job_root, 'steps', 'unit', 'profile')
    if os.path.exists(prof_path):
        text = _read_features_text(prof_path)
        geoms, _, _, _, _, _ = _parse_features_text(text, 1.0, set())
        print("Unit Profile Geometries 0010:")
        for g in geoms:
            print(f"  Bounds: {g.bounds}")
        min_x = min(g.bounds[0] for g in geoms)
        max_x = max(g.bounds[2] for g in geoms)
        min_y = min(g.bounds[1] for g in geoms)
        max_y = max(g.bounds[3] for g in geoms)
        print(f"Total Bounds: min_x={min_x}, min_y={min_y}, max_x={max_x}, max_y={max_y}")
        print(f"W={max_x-min_x}, H={max_y-min_y}")
finally:
    shutil.rmtree(temp_dir)
