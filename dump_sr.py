import sys
import os
import tarfile
import tempfile
import shutil

sys.path.insert(0, os.path.abspath('.'))
from core.pipeline import _parse_step_repeat

temp_dir = tempfile.mkdtemp()
try:
    with tarfile.open('fhr0010_bkm.tgz', 'r:gz') as tar:
        tar.extractall(temp_dir)
    job_root = [os.path.join(temp_dir, d) for d in os.listdir(temp_dir) if os.path.isdir(os.path.join(temp_dir, d)) and d != '__MACOSX'][0]
    sh = _parse_step_repeat(job_root, 1.0)
    for k, v in sh.items():
        print(f"Step {k}:")
        max_abs = 0.0
        for sr in v:
            max_abs = max(max_abs, abs(sr.x), abs(sr.y), sr.dx if sr.dx>0 else 0.0, sr.dy if sr.dy>0 else 0.0)
        print(f"  Max SR Abs: {max_abs}")
finally:
    shutil.rmtree(temp_dir)
