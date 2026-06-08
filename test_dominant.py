import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from core.pipeline import _render_pipeline

with open('fhr0020_bkm.tgz', 'rb') as f:
    data = f.read()
obj = _render_pipeline(data, 'fhr0020_bkm.tgz', None)
if obj and obj.panel_layout:
    print(f"Dominant Angle: {obj.panel_layout.dominant_angle}")
