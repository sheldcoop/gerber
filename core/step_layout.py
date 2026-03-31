"""
core/step_layout.py — Step-repeat hierarchy processing and unit position computation.
"""


def compute_unit_positions(step_hierarchy: dict, unit_bounds: tuple,
                           panel_width: float = None,
                           panel_height: float = None):
    """Walk the STEP-REPEAT hierarchy and compute absolute (x, y) for every unit.

    Recursively multiplies out NX×NY at each level from the top step (panel)
    down to the leaf step (unit).

    Args:
        step_hierarchy: Dict from _parse_step_repeat() — {step_name: [StepRepeat, ...]}.
        unit_bounds: (width_mm, height_mm) of a single unit.
        panel_width: Panel frame width in mm — derived from ODB++ panel profile.
            If None, uses the content bounding box (no fixed-frame assumption).
        panel_height: Panel frame height in mm — derived from ODB++ panel profile.
            If None, uses the content bounding box (no fixed-frame assumption).

    Returns:
        PanelLayout with all unit positions and derived grid info.
    """
    # Lazy import to avoid circular dependency
    from gerber_renderer import PanelLayout

    # Find the top-level step (the one not referenced as a child by anyone)
    all_children = set()
    all_parents = set()
    for parent, repeats in step_hierarchy.items():
        all_parents.add(parent)
        for sr in repeats:
            all_children.add(sr.child_step.lower())

    # Top step = parent that is not a child of anyone else
    top_steps = all_parents - all_children
    # If no clear top, try 'panel', then pick the one with most hierarchy depth
    if not top_steps:
        top_step = 'panel' if 'panel' in step_hierarchy else next(iter(step_hierarchy), None)
    elif len(top_steps) == 1:
        top_step = top_steps.pop()
    else:
        # Prefer 'panel' if available
        top_step = 'panel' if 'panel' in top_steps else sorted(top_steps)[0]

    if top_step is None:
        return PanelLayout(
            unit_positions=[(0, 0)], unit_bounds=unit_bounds,
            total_units=1, rows=1, cols=1,
            step_hierarchy=step_hierarchy,
            panel_width=panel_width, panel_height=panel_height,
        )

    def _expand(step_name: str, offset_x: float, offset_y: float) -> list:
        """Recursively expand step-repeat placements, returning leaf (unit) positions."""
        repeats = step_hierarchy.get(step_name.lower(), [])
        if not repeats:
            # Leaf step (unit) — return this position
            return [(offset_x, offset_y)]

        positions = []
        for sr in repeats:
            for iy in range(sr.ny):
                for ix in range(sr.nx):
                    child_x = offset_x + sr.x + ix * sr.dx
                    child_y = offset_y + sr.y + iy * sr.dy
                    positions.extend(_expand(sr.child_step, child_x, child_y))
        return positions

    # Start expansion from top step at origin
    positions = _expand(top_step, 0.0, 0.0)

    # Deduplicate (floating point tolerance)
    seen = set()
    unique = []
    for px, py in positions:
        key = (round(px, 3), round(py, 3))
        if key not in seen:
            seen.add(key)
            unique.append((px, py))

    # Derive rows/cols from unique Y/X values
    if unique:
        xs = sorted(set(round(p[0], 2) for p in unique))
        ys = sorted(set(round(p[1], 2) for p in unique))
        cols = len(xs)
        rows = len(ys)
    else:
        rows, cols = 1, 1

    # Save raw (pre-centering) positions for AOI coordinate normalisation.
    # AOI X_MM/Y_MM are in the same ODB++ coordinate space as these raw positions.
    raw_unique = list(unique)

    # Shift positions so they match the physical panel coordinate system.
    # panel_width/height come from the ODB++ panel step profile (authoritative).
    # If not available, use content bounds — content starts at (0, 0).
    pw = panel_width  if panel_width  is not None else 510.0
    ph = panel_height if panel_height is not None else 515.0
    if unique:
        uw, uh = unit_bounds
        raw_min_x = min(p[0] for p in unique)
        raw_max_x = max(p[0] for p in unique) + uw
        raw_min_y = min(p[1] for p in unique)
        raw_max_y = max(p[1] for p in unique) + uh
        content_w = raw_max_x - raw_min_x
        content_h = raw_max_y - raw_min_y
        shift_x = (pw - content_w) / 2.0 - raw_min_x
        shift_y = (ph - content_h) / 2.0 - raw_min_y
        unique = [(px + shift_x, py + shift_y) for px, py in unique]

    return PanelLayout(
        unit_positions=unique,
        unit_positions_raw=raw_unique,
        unit_bounds=unit_bounds,
        total_units=len(unique),
        rows=rows,
        cols=cols,
        step_hierarchy=step_hierarchy,
        panel_width=pw if unique else (panel_width or 0),
        panel_height=ph if unique else (panel_height or 0),
    )
