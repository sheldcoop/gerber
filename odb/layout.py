"""odb/layout.py — Profile, component, and main parse_odb_archive pipeline."""

import os
import re
import shutil
from typing import Optional

from odb.constants import INCHES_TO_MM
from odb.models import ODBLayer, DrillHit, ComponentPlacement, ParsedODB
from odb.archive import (
    extract_tgz, read_units, read_design_origin,
    parse_matrix, scan_layers_dir, find_step, parse_step_repeat,
)
from odb.symbols import detect_symbol_scale, load_user_symbols
from odb.features import (
    read_features_text, parse_features_text,
    compute_bounds, aggregate_bounds,
)

_AREA_LAYER_RE = re.compile(
    r'flex.?area|bend.?area|rigid.?area|board.?area|panel.?area',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Profile parser
# ---------------------------------------------------------------------------

def parse_profile_layer(job_root: str, step_name: str, uf: float,
                         unknown_symbols: set) -> Optional[ODBLayer]:
    """
    Parse the step-level board outline from steps/<step>/profile.
    Returns an ODBLayer with layer_type='outline', or None if not found.
    """
    profile_path = os.path.join(job_root, 'steps', step_name, 'profile')
    text = read_features_text(profile_path)
    if text is None:
        return None

    geoms, widths, warnings, _, _ = parse_features_text(text, uf, unknown_symbols)
    if not geoms:
        return None

    bounds = compute_bounds(geoms)
    return ODBLayer(
        name='profile',
        layer_type='outline',
        polygons=geoms,
        bounds=bounds,
        polygon_count=len(geoms),
        trace_widths=widths,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Component parser
# ---------------------------------------------------------------------------

def parse_components(job_root: str, step_name: str, uf: float) -> list:
    """
    Parse component placement data from ODB++ steps/<step>/components file.

    ODB++ format:
        TOP
        CMP <idx> <x> <y> <rotation> <mirror> <refdes> <part_type> [;attrs]
        BOT
        CMP ...

    Returns list of ComponentPlacement.
    """
    placements = []

    candidates = [
        os.path.join(job_root, 'steps', step_name, 'components'),
        os.path.join(job_root, 'steps', step_name, 'comp+top'),
        os.path.join(job_root, 'steps', step_name, 'comp+bot'),
    ]
    layers_dir = os.path.join(job_root, 'steps', step_name, 'layers')
    if os.path.isdir(layers_dir):
        for entry in os.listdir(layers_dir):
            if re.search(r'comp|component', entry, re.I):
                comp_path = os.path.join(layers_dir, entry, 'components')
                if os.path.isfile(comp_path):
                    candidates.append(comp_path)

    for comp_path in candidates:
        if not os.path.isfile(comp_path):
            continue
        try:
            with open(comp_path, 'r', errors='ignore') as f:
                content = f.read()
        except (OSError, IOError):
            continue

        current_side = 'T'
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            upper = line.upper()
            if upper == 'TOP':
                current_side = 'T'
                continue
            elif upper == 'BOT':
                current_side = 'B'
                continue

            line = line.split(';')[0].strip()
            parts = line.split()
            if not parts or parts[0].upper() != 'CMP' or len(parts) < 7:
                continue

            try:
                x        = float(parts[2]) * uf
                y        = float(parts[3]) * uf
                rotation = float(parts[4])
                mirror   = parts[5].upper() in ('M', 'Y', '1')
                refdes   = parts[6]
                part_type = parts[7] if len(parts) > 7 else ''
                side = 'B' if mirror else current_side

                placements.append(ComponentPlacement(
                    refdes=refdes, part_type=part_type,
                    x=x, y=y, rotation=rotation, mirror=mirror, side=side,
                ))
            except (ValueError, IndexError):
                continue

        if placements:
            break

    return placements


# ---------------------------------------------------------------------------
# Main public entry point
# ---------------------------------------------------------------------------

def parse_odb_archive(data: bytes, filename: str = '') -> ParsedODB:
    """
    Parse an ODB++ .tgz archive into a ParsedODB object.

    Args:
        data:     raw bytes of the .tgz file
        filename: original filename (used only in error messages)

    Returns:
        ParsedODB with layers dict and board_bounds in mm

    Raises:
        ValueError if the archive cannot be opened
    """
    global_warnings = []
    layers = {}

    try:
        tmp_dir, job_root = extract_tgz(data)
    except Exception as e:
        raise ValueError(f"Cannot open ODB++ archive '{filename}': {e}") from e

    try:
        # 1. Units
        units = read_units(job_root)
        uf = INCHES_TO_MM if units == 'inch' else 1.0
        origin_x, origin_y = read_design_origin(job_root, uf)
        unknown_symbols = set()
        all_fiducials = []
        all_drill_hits = []

        # 2. Step & layer list
        step_name  = find_step(job_root)
        layers_dir = os.path.join(job_root, 'steps', step_name, 'layers')

        matrix_layers = parse_matrix(job_root)
        if not matrix_layers:
            global_warnings.append("matrix/matrix not found or empty — scanning layers directory")
            matrix_layers = scan_layers_dir(layers_dir)

        # 3. Pre-load user-defined symbols
        user_sym_map = load_user_symbols(job_root, uf)

        # 4. Parse each layer
        for layer_name, layer_type in matrix_layers:
            if layer_type == 'other':
                continue
            if _AREA_LAYER_RE.search(layer_name):
                continue

            features_path = os.path.join(layers_dir, layer_name, 'features')
            text = read_features_text(features_path)
            if text is None:
                global_warnings.append(f"Layer '{layer_name}': features file not found, skipping")
                continue

            from odb.archive import read_units as _ru  # avoid circular; units_from_text is in archive
            from odb.archive import units_from_text
            file_units = units_from_text(text)
            layer_uf = (INCHES_TO_MM if file_units == 'inch' else 1.0) if file_units else uf

            sym_scale = detect_symbol_scale(text, layer_uf)
            if sym_scale != 1.0:
                global_warnings.append(
                    f"Layer '{layer_name}': symbol sizes appear to be in mils. "
                    f"Applying ×{sym_scale} correction."
                )

            try:
                geoms, widths, layer_warnings, fiducials, layer_drill_hits = \
                    parse_features_text(text, layer_uf, unknown_symbols, user_sym_map, sym_scale)
                all_fiducials.extend(fiducials)
                if layer_type == 'drill':
                    for hx, hy, diam in layer_drill_hits:
                        all_drill_hits.append(DrillHit(x=hx, y=hy, diameter=diam, layer_name=layer_name))
            except Exception as e:
                global_warnings.append(f"Layer '{layer_name}': parse error — {e}, skipping")
                continue

            if not geoms:
                global_warnings.append(f"Layer '{layer_name}': no geometry parsed")
                continue

            bounds = compute_bounds(geoms)
            layers[layer_name] = ODBLayer(
                name=layer_name,
                layer_type=layer_type,
                polygons=geoms,
                bounds=bounds,
                polygon_count=len(geoms),
                trace_widths=widths,
                warnings=layer_warnings,
            )

        # 5. Board profile (outline)
        profile_layer = parse_profile_layer(job_root, step_name, uf, unknown_symbols)
        if profile_layer is not None:
            layers['profile'] = profile_layer
        else:
            global_warnings.append("Board profile not found — outline layer unavailable")

        # 6. Component placements
        all_components = parse_components(job_root, step_name, uf)

        # 7. Aggregate board bounds
        board_bounds = aggregate_bounds(layers)

        return ParsedODB(
            layers=layers,
            board_bounds=board_bounds,
            step_name=step_name,
            units=units,
            origin_x=origin_x,
            origin_y=origin_y,
            unknown_symbols=unknown_symbols,
            fiducials=all_fiducials,
            drill_hits=all_drill_hits,
            components=all_components,
            warnings=global_warnings,
        )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
