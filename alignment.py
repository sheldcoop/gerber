"""
alignment.py — facade for the defect-to-design alignment package.

The implementation is split by responsibility:
  - alignment_geometry  : panel geometry constants + cell/origin geometry
  - alignment_fiducials : fiducial detection / pairing / confidence
  - alignment_core      : AlignmentResult + alignment algorithms + cached wrappers

This module re-exports the public + historically-imported names so existing
`from alignment import …` call sites (and tests) keep working unchanged.
"""

from alignment_geometry import (
    FRAME_WIDTH, FRAME_HEIGHT, FIXED_GAP_X, FIXED_GAP_Y, INTER_UNIT_GAP,
    GeometryContext, calculate_geometry,
    calculate_physical_unit_origin, get_panel_quadrant_bounds,
)
from alignment_fiducials import (
    FIDUCIAL_COLUMNS, detect_fiducials, _pair_fiducials, _compute_confidence,
)
from alignment_core import (
    AlignmentResult,
    _compute_overlap, _align_simple, _align_affine,
    compute_alignment, apply_alignment, get_debug_info,
    _alignment_result_to_hashable, compute_alignment_cached,
    _dict_to_alignment_result, apply_alignment_cached, compute_dataframe_hash,
)

__all__ = [
    'FRAME_WIDTH', 'FRAME_HEIGHT', 'FIXED_GAP_X', 'FIXED_GAP_Y', 'INTER_UNIT_GAP',
    'GeometryContext', 'calculate_geometry',
    'calculate_physical_unit_origin', 'get_panel_quadrant_bounds',
    'FIDUCIAL_COLUMNS', 'detect_fiducials', '_pair_fiducials', '_compute_confidence',
    'AlignmentResult',
    '_compute_overlap', '_align_simple', '_align_affine',
    'compute_alignment', 'apply_alignment', 'get_debug_info',
    '_alignment_result_to_hashable', 'compute_alignment_cached',
    '_dict_to_alignment_result', 'apply_alignment_cached', 'compute_dataframe_hash',
]
