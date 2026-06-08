import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from core.pipeline import _render_pipeline

with open('fhr0020_bkm.tgz', 'rb') as f:
    data = f.read()
obj = _render_pipeline(data, 'fhr0020_bkm.tgz', None)
if obj and obj.panel_layout:
    pl = obj.panel_layout
    print(f"Unit Width: {pl.unit_bounds[0]}, Unit Height: {pl.unit_bounds[1]}")
    print(f"Hierarchy:")
    for k, v in pl.step_hierarchy.items():
        print(f"  {k}: {len(v)} placements")
