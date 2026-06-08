import os
import tarfile
import tempfile
import shutil
import sys

tgz_path = 'fhr0020_bkm.tgz'
if not os.path.exists(tgz_path):
    print(f"File {tgz_path} not found.")
    sys.exit(1)

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
        
    # Append current dir to path to import core
    sys.path.insert(0, os.path.abspath('.'))
    from core.pipeline import _parse_step_repeat, _read_features_text, _parse_features_text
    
    # 1. Parse steps to find hierarchy
    steps_dir = os.path.join(job_root, 'steps')
    panel_step = None
    if os.path.exists(steps_dir):
        steps = os.listdir(steps_dir)
        print("Steps found:", steps)
        for s in steps:
            if s.lower() in ('panel', 'pnl', 'array'):
                panel_step = s
                break
        if not panel_step:
            panel_step = steps[0]
            
    print(f"Panel step: {panel_step}")
    
    # Parse hierarchy
    step_hierarchy = _parse_step_repeat(job_root, 1.0)
    print("Initial Step Hierarchy:")
    for parent, children in step_hierarchy.items():
        print(f"  {parent}:")
        for sr in children:
            print(f"    -> {sr.child_step} (nx={sr.nx}, ny={sr.ny}, dx={sr.dx}, dy={sr.dy}, x={sr.x}, y={sr.y}, rot={sr.angle})")
            
    # Find unit step bounds
    unit_step = None
    for parent, children in step_hierarchy.items():
        if not children:
            unit_step = parent
            break
            
    if not unit_step:
        parents = set(step_hierarchy.keys())
        children = set()
        for sr_list in step_hierarchy.values():
            for sr in sr_list:
                children.add(sr.child_step.lower())
        leaves = children - parents
        if leaves:
            unit_step = list(leaves)[0]
            
    print(f"Unit step: {unit_step}")

    # Quick profile parse for unit bounds
    unit_w, unit_h = 43.5, 27.5 # Fallback
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
    print(f"Unit dimensions (profile): {unit_w} x {unit_h}")
                    
    # Re-run inch quirk logic
    _all_spacings = []
    for _sr_list in step_hierarchy.values():
        for _sr in _sr_list:
            if _sr.dx > 0: _all_spacings.append(_sr.dx)
            if _sr.dy > 0: _all_spacings.append(_sr.dy)
            
    quirk_applied = False
    if _all_spacings and unit_w > 10:
        if min(_all_spacings) < 5.0 and min(_all_spacings) * 25.4 > unit_w * 0.8:
            print("Applying legacy inch quirk!")
            step_hierarchy = _parse_step_repeat(job_root, 25.4)
            quirk_applied = True
            
    # Post profile inch quirk
    if not quirk_applied and unit_w > 10.0:
        _sr_max_abs = 0.0
        for _sr_list in step_hierarchy.values():
            for _sr in _sr_list:
                _sr_max_abs = max(_sr_max_abs, abs(_sr.x), abs(_sr.y), _sr.dx, _sr.dy)
        if 0 < _sr_max_abs < unit_w:
            print(f"Applying post-profile inch quirk! _sr_max_abs={_sr_max_abs} < unit_w={unit_w}")
            step_hierarchy = _parse_step_repeat(job_root, 25.4)
            quirk_applied = True
            
    if not quirk_applied:
        print("NO inch quirk applied.")

    # Compute unit positions
    from core.step_layout import compute_unit_positions
    panel_layout = compute_unit_positions(step_hierarchy, (unit_w, unit_h))
    
    positions = panel_layout.unit_positions
    angle = getattr(panel_layout, 'dominant_angle', 0.0)
    print(f"Panel Layout => Total units: {panel_layout.total_units}, Rows: {panel_layout.rows}, Cols: {panel_layout.cols}")
    print(f"Dominant angle: {angle}")
    print("First 5 positions:", positions[:5])
    
finally:
    shutil.rmtree(temp_dir)
