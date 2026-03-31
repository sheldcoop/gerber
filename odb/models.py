"""odb/models.py — Dataclasses for the ODB++ parser."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class _ODBSymbol:
    """Aperture/symbol definition from the $ table in a features file."""
    shape: str      # 'round', 'square', 'rect', 'oval', 'diamond', 'unknown', 'skip'
    size_x: float   # primary size: diameter (round), side (square), width (rect/oval)
    size_y: float   # secondary size: height for rect/oval; equals size_x otherwise
    raw_desc: str = ''
    corner_r: float = 0.0  # corner radius for rounded_rect


@dataclass
class ODBLayer:
    """
    Single parsed ODB++ layer — mirrors GerberLayer interface so visualizer.py
    works unchanged.
    """
    name: str
    layer_type: str      # 'copper', 'soldermask', 'silkscreen', 'paste', 'drill', 'outline', 'other'
    polygons: list       # list of Shapely geometry objects
    bounds: tuple        # (xmin, ymin, xmax, ymax) in mm
    polygon_count: int = 0
    trace_widths: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class DrillHit:
    """Single drill hit extracted from a drill layer."""
    x: float
    y: float
    diameter: float   # mm
    layer_name: str = ''


@dataclass
class ComponentPlacement:
    """Single component placement record from ODB++ components file."""
    refdes: str
    part_type: str
    x: float        # center X in mm
    y: float        # center Y in mm
    rotation: float # degrees
    mirror: bool    # True = bottom side
    side: str       # 'T' or 'B'


@dataclass
class ParsedODB:
    """Result of parsing an ODB++ archive."""
    layers: dict         # {layer_name: ODBLayer}
    board_bounds: tuple  # (xmin, ymin, xmax, ymax) in mm
    step_name: str
    units: str           # 'mm' or 'inch'
    origin_x: float = 0.0
    origin_y: float = 0.0
    unknown_symbols: set = field(default_factory=set)
    fiducials: list = field(default_factory=list)
    drill_hits: list = field(default_factory=list)   # list[DrillHit]
    components: list = field(default_factory=list)   # list[ComponentPlacement]
    warnings: list = field(default_factory=list)


@dataclass
class StepRepeat:
    """A single STEP-REPEAT entry from an ODB++ stephdr file."""
    child_step: str  # name of the child step being placed
    x: float         # origin offset X (mm)
    y: float         # origin offset Y (mm)
    dx: float        # repeat spacing X (mm)
    dy: float        # repeat spacing Y (mm)
    nx: int          # number of repeats in X
    ny: int          # number of repeats in Y
    angle: float = 0.0


# ODB++ matrix TYPE → internal layer_type mapping
MATRIX_TYPE_MAP = {
    'SIGNAL':       'copper',
    'POWER':        'copper',
    'MIXED':        'copper',
    'POWER_GROUND': 'copper',
    'SOLDER_MASK':  'soldermask',
    'MASK':         'soldermask',
    'SILK_SCREEN':  'silkscreen',
    'SOLDER_PASTE': 'paste',
    'DRILL':        'drill',
    'ROUT':         'drill',
    'PROFILE':      'outline',
    'DIELECTRIC':   'other',
    'DOCUMENT':     'other',
    'COMPONENT':    'other',
}
