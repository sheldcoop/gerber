"""odb/constants.py — Named constants for the ODB++ parser."""

# Unit conversion
INCHES_TO_MM = 25.4
MILS_TO_MM = 0.0254

# Geometry approximation resolutions (segment count for circular arcs/buffers)
ARC_SEGMENTS_COARSE = 8   # small features < 0.5 mm radius
ARC_SEGMENTS_FINE = 16    # larger features

# _parse_features_text: stop accumulating per-feature warnings after this many
MAX_FEATURE_ERRORS = 50

# Near-zero threshold for coordinate equality checks
COORD_EPSILON = 1e-9

# _detect_symbol_scale: if max_symbol_size > max_coord × this ratio, symbols are in mils
MIL_DETECT_RATIO = 2.0

# _symbol_to_geometry shape-specific constants
SPOKE_WIDTH_FACTOR = 0.15   # thermal relief spoke width = (outer - inner) × factor
CROSS_ARM_FACTOR = 0.25     # cross pad arm width = min(w, h) × factor
DEFAULT_CORNER_RADIUS = 0.2 # rounded_rect fallback: min(w,h) × factor when r not specified
MIN_FEATURE_SIZE = 0.02     # mm — minimum geometry size to prevent degenerate shapes

# _detect_symbol_scale: near-zero coordinate threshold
COORD_NONZERO_EPSILON = 1e-6

# Header scan limit for unit detection in features files
UNITS_HEADER_SCAN_LINES = 30
