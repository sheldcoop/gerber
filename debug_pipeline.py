import sys
import os

sys.path.insert(0, os.path.abspath('.'))
from core.pipeline import _render_pipeline

with open('fhr0010_bkm.tgz', 'rb') as f:
    data = f.read()
obj = _render_pipeline(data, 'fhr0010_bkm.tgz', None)
if obj and obj.panel_layout:
    pl = obj.panel_layout
    print(f"Panel Layout Unit Positions: {len(pl.unit_positions)}")
    if pl.unit_positions:
        print(f"First position: {pl.unit_positions[0]}")
    else:
        print("Empty unit positions list")
else:
    print("No panel layout!")
