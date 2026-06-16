"""
odb/ — ODB++ parser package.

Public API (clean names):
    parse_odb_archive, extract_tgz, read_units, units_from_text,
    read_design_origin, parse_matrix, scan_layers_dir, find_step,
    parse_step_repeat, read_features_text, parse_features_text,
    compute_bounds, aggregate_bounds, detect_symbol_scale,
    load_user_symbols, parse_symbol_table, arc_to_points,

Data classes:
    ODBLayer, DrillHit, ComponentPlacement, ParsedODB, StepRepeat, _ODBSymbol

Backward-compatible underscore aliases are provided so existing callers
(gerber_renderer.py, core/pipeline.py, core/layer_renderer.py, ui/sidebar.py)
continue to work without any changes.
"""

from odb.models import (
    _ODBSymbol,
    ODBLayer,
    DrillHit,
    ComponentPlacement,
    ParsedODB,
    StepRepeat,
    MATRIX_TYPE_MAP,
)

from odb.archive import (
    extract_tgz,
    read_units,
    units_from_text,
    read_design_origin,
    parse_matrix,
    scan_layers_dir,
    find_step,
    parse_step_repeat,
)

from odb.symbols import (
    parse_symbol_descriptor,
    parse_symbol_table,
    parse_symbol_table_from_text,
    detect_symbol_scale,
    load_user_symbols,
)

from odb.geometry import (
    symbol_to_geometry,
    arc_to_points,
    parse_pad_record,
    parse_line_record,
    parse_arc_record,
    parse_surface_block,
)

from odb.features import (
    read_features_text,
    parse_features_text,
    compute_bounds,
    aggregate_bounds,
)

from odb.layout import (
    parse_profile_layer,
    parse_components,
    parse_odb_archive,
)


# ---------------------------------------------------------------------------
# Backward-compatible underscore aliases
# Existing callers use names like _extract_odb_tgz, _parse_matrix, etc.
# These aliases let all callers work without any import changes.
# ---------------------------------------------------------------------------

_extract_odb_tgz            = extract_tgz
_read_units                 = read_units
_units_from_text            = units_from_text
_read_design_origin         = read_design_origin
_parse_matrix               = parse_matrix
_scan_layers_dir            = scan_layers_dir
_find_step                  = find_step
_parse_step_repeat          = parse_step_repeat
_parse_symbol_descriptor    = parse_symbol_descriptor
_parse_symbol_table         = parse_symbol_table
_parse_symbol_table_from_text = parse_symbol_table_from_text
_detect_symbol_scale        = detect_symbol_scale
_load_user_symbols          = load_user_symbols
_symbol_to_geometry         = symbol_to_geometry
_odb_arc_to_points          = arc_to_points
_parse_pad_record           = parse_pad_record
_parse_line_record          = parse_line_record
_parse_arc_record           = parse_arc_record
_parse_surface_block        = parse_surface_block
_read_features_text         = read_features_text
_parse_features_text        = parse_features_text
_compute_bounds             = compute_bounds
_aggregate_bounds           = aggregate_bounds
_parse_profile_layer        = parse_profile_layer
_parse_components           = parse_components
