import os
import glob
import pandas as pd
import numpy as np

# Find the first TGZ file in the directory
tgz_files = glob.glob('*.tgz')
if not tgz_files:
    print("No TGZ file found to extract exact coordinates.")
    exit(1)

tgz_path = tgz_files[0]
print(f"Extracting exact unit coordinates from {tgz_path}...")

from core.job_registry import JobRegistry
import tempfile
import shutil

# Unpack TGZ
temp_dir = tempfile.mkdtemp()
try:
    import tarfile
    with tarfile.open(tgz_path, 'r:gz') as tar:
        tar.extractall(temp_dir)
        
    job_root = None
    for item in os.listdir(temp_dir):
        if os.path.isdir(os.path.join(temp_dir, item)) and item != '__MACOSX':
            job_root = os.path.join(temp_dir, item)
            break
            
    if not job_root:
        job_root = temp_dir
        
    from core.pipeline import _parse_step_repeat, _read_features_text, _parse_features_text
    
    # 1. Parse steps to find hierarchy
    steps_dir = os.path.join(job_root, 'steps')
    panel_step = None
    if os.path.exists(steps_dir):
        steps = os.listdir(steps_dir)
        for s in steps:
            if s.lower() in ('panel', 'pnl', 'array'):
                panel_step = s
                break
        if not panel_step:
            panel_step = steps[0]
            
    # Parse hierarchy
    step_hierarchy = _parse_step_repeat(job_root, 1.0)
    
    # Run the inch quirk logic
    # Find unit step bounds
    unit_step = None
    for parent, children in step_hierarchy.items():
        if not children:
            unit_step = parent
            break
            
    if not unit_step:
        # Fallback leaf detection
        parents = set(step_hierarchy.keys())
        children = set()
        for sr_list in step_hierarchy.values():
            for sr in sr_list:
                children.add(sr.child_step.lower())
        leaves = children - parents
        if leaves:
            unit_step = list(leaves)[0]

    # Quick profile parse for unit bounds
    unit_w, unit_h = 43.5, 27.5 # Fallback defaults
    if unit_step:
        prof_path = os.path.join(steps_dir, unit_step, 'profile')
        if os.path.exists(prof_path):
            text = _read_features_text(prof_path)
            if text:
                geoms, _, _, _, _, detected_uf = _parse_features_text(text, 1.0, set())
                if geoms:
                    min_x = min(g.bounds[0] for g in geoms)
                    max_x = max(g.bounds[2] for g in geoms)
                    min_y = min(g.bounds[1] for g in geoms)
                    max_y = max(g.bounds[3] for g in geoms)
                    unit_w = max_x - min_x
                    unit_h = max_y - min_y
                    
    # Re-run inch quirk
    _all_spacings = []
    for _sr_list in step_hierarchy.values():
        for _sr in _sr_list:
            if _sr.dx > 0: _all_spacings.append(_sr.dx)
            if _sr.dy > 0: _all_spacings.append(_sr.dy)
            
    if _all_spacings and unit_w > 10:
        if min(_all_spacings) < 5.0 and min(_all_spacings) * 25.4 > unit_w * 0.8:
            step_hierarchy = _parse_step_repeat(job_root, 25.4)
            
    # Post profile inch quirk
    if unit_w > 10.0:
        _sr_max_abs = 0.0
        for _sr_list in step_hierarchy.values():
            for _sr in _sr_list:
                _sr_max_abs = max(_sr_max_abs, abs(_sr.x), abs(_sr.y), _sr.dx, _sr.dy)
        if 0 < _sr_max_abs < unit_w:
            step_hierarchy = _parse_step_repeat(job_root, 25.4)

    # Compute unit positions
    from core.step_layout import compute_unit_positions
    panel_layout = compute_unit_positions(step_hierarchy, (unit_w, unit_h))
    
    positions = panel_layout.unit_positions
    angle = getattr(panel_layout, 'dominant_angle', 0.0)
    print(f"Extracted {len(positions)} unit positions. Dominant angle: {angle}")
    
    # Generate perfect Excel file
    data = []
    defect_types = ['Short', 'Open', 'Pin Hole']
    verifications = ['CU22', 'SH', 'OP']
    
    # Unique rows/cols
    uniq_x = sorted(set(round(p[0], 2) for p in positions))
    uniq_y = sorted(set(round(p[1], 2) for p in positions))
    
    for ux, uy in positions:
        col = uniq_x.index(round(ux, 2))
        row = uniq_y.index(round(uy, 2))
        
        # Systematic short at local (12.5, 18.2) -> converted back from rotation!
        # If angle=270, dy = -x, dx = y => so panel dx = y, panel dy = -x
        # For simplicity, we just add panel offsets
        # Wait, if angle=270, ax = -dy, ay = dx
        # so dx = ay, dy = -ax
        ax, ay = 12.5, 18.2
        if angle == 270:
            dx, dy = ay, -ax
        elif angle == 90:
            dx, dy = -ay, ax
        elif angle == 180:
            dx, dy = -ax, -ay
        else:
            dx, dy = ax, ay
            
        if np.random.rand() > 0.1:
            data.append({
                'DEFECT_TYPE': 'Short',
                'X_COORDINATES': ux + dx,
                'Y_COORDINATES': uy + dy,
                'VERIFICATION': 'SH',
                'UNIT_INDEX_X': col,
                'UNIT_INDEX_Y': row,
            })
            
        # Systematic open at local (5.1, 10.5)
        ax2, ay2 = 5.1, 10.5
        if angle == 270:
            dx2, dy2 = ay2, -ax2
        elif angle == 90:
            dx2, dy2 = -ay2, ax2
        elif angle == 180:
            dx2, dy2 = -ax2, -ay2
        else:
            dx2, dy2 = ax2, ay2
            
        if np.random.rand() > 0.5:
            data.append({
                'DEFECT_TYPE': 'Open',
                'X_COORDINATES': ux + dx2,
                'Y_COORDINATES': uy + dy2,
                'VERIFICATION': 'OP',
                'UNIT_INDEX_X': col,
                'UNIT_INDEX_Y': row,
            })
            
        # Noise
        for _ in range(np.random.randint(0, 3)):
            data.append({
                'DEFECT_TYPE': np.random.choice(defect_types),
                'X_COORDINATES': ux + np.random.uniform(5, 20),
                'Y_COORDINATES': uy + np.random.uniform(5, 20),
                'VERIFICATION': np.random.choice(verifications),
                'UNIT_INDEX_X': col,
                'UNIT_INDEX_Y': row,
            })
            
    df = pd.DataFrame(data)
    df.to_excel('test_commonality_BU-02F.xlsx', index=False)
    print("Successfully created test_commonality_BU-02F.xlsx with PERFECT unit offsets!")
    
finally:
    shutil.rmtree(temp_dir)
