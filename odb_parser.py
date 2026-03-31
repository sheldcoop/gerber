"""
odb_parser.py — backward-compatible shim.

All implementation has moved to the odb/ package.
This file re-exports everything so existing callers continue to work unchanged.
"""
from odb import *  # noqa: F401, F403
from odb import (
    # Data classes
    _ODBSymbol, ODBLayer, DrillHit, ComponentPlacement, ParsedODB, StepRepeat,
    # Public functions
    parse_odb_archive,
    # Underscore aliases (used by gerber_renderer, core/pipeline, core/layer_renderer)
    _extract_odb_tgz, _read_units, _units_from_text, _read_design_origin,
    _parse_matrix, _scan_layers_dir, _find_step, _parse_step_repeat,
    _parse_symbol_descriptor, _parse_symbol_table, _parse_symbol_table_from_text,
    _detect_symbol_scale, _load_user_symbols,
    _symbol_to_geometry, _odb_arc_to_points,
    _parse_pad_record, _parse_line_record, _parse_surface_block,
    _read_features_text, _parse_features_text,
    _compute_bounds, _aggregate_bounds,
    _parse_profile_layer, _parse_components,
)
