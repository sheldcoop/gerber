"""
aoi_loader.py — AOI (Automated Optical Inspection) defect data loader.

Loads Orbotech AOI defect data from Excel files, auto-detects column mappings,
extracts buildup layer, side (Front/Back), panel number and section from filenames,
and converts coordinates from microns to mm.

Filename convention (new format):
  BU_01F_Panel1_S1.xlsx  → Buildup 1, Front, Panel 1, Section 1
  BU_02B_Panel2_S3.xlsx  → Buildup 2, Back,  Panel 2, Section 3
  BU_01F_Panel1.xlsx     → Buildup 1, Front, Panel 1, Section 1 (section optional)

  Multiple section files for the same Panel+BU+Side are merged automatically:
    BU_01F_Panel1_S1.xlsx  ┐
    BU_01F_Panel1_S2.xlsx  ├─ all merged as Panel 1, Buildup 1, Front
    BU_01F_Panel1_S3.xlsx  ┘

Legacy filename convention (still supported):
  BU-02F  → Buildup layer 2, Front side  (assumes Panel_01, Section 1)
  BU-02B  → Buildup layer 2, Back side
  BU-01 F → Also accepted (space before F/B)

Expected Excel columns (auto-detected by alias matching):
  DEFECT_ID, DEFECT_TYPE, X_COORDINATES, Y_COORDINATES,
  UNIT_INDEX_X, UNIT_INDEX_Y, MODALITY_1, MODALITY_2,
  ENHANCED_IMAGE, VERIFICATION
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex to extract buildup number and side from filename
# New format:  BU_01F_Panel1_S2  or  BU_01F_Panel1  (section optional)
# Legacy format: BU-02F, BU-02B, BU-02 F, bu-1f, BU02F, etc.
FILENAME_PATTERN_NEW = re.compile(
    r'BU[_\-]?(\d{1,2})\s*([FfBb])[_\-]Panel(\d+)(?:[_\-]S(\d+))?',
    re.IGNORECASE
)
FILENAME_PATTERN_LEGACY = re.compile(r'BU[-_]?(\d{1,2})\s*([FfBb])', re.IGNORECASE)
# Backward-compatible alias — sidebar.py and tests still import this name
FILENAME_PATTERN = FILENAME_PATTERN_LEGACY

# Column name aliases for auto-detection (canonical name → list of aliases)
# All comparisons done in lowercase with spaces/underscores normalized
COLUMN_ALIASES = {
    'DEFECT_ID': [
        'defect_id', 'defectid', 'defect id', 'id', 'def_id',
    ],
    'DEFECT_TYPE': [
        'defect_type', 'defecttype', 'defect type', 'type', 'def_type',
        'defect_name', 'defectname',
    ],
    'X_COORDINATES': [
        'x_coordinates', 'x_coord', 'x_coordinate', 'xcoord',
        'x', 'x_um', 'x_pos', 'xposition', 'x_position',
    ],
    'Y_COORDINATES': [
        'y_coordinates', 'y_coord', 'y_coordinate', 'ycoord',
        'y', 'y_um', 'y_pos', 'yposition', 'y_position',
    ],
    'UNIT_INDEX_X': [
        'unit_index_x', 'unitx', 'unit_x', 'unitindexr', 'unit_index_r',
        'col', 'column_index', 'die_x', 'diex',
    ],
    'UNIT_INDEX_Y': [
        'unit_index_y', 'unity', 'unit_y', 'unitindexc', 'unit_index_c',
        'row', 'row_index', 'die_y', 'diey',
    ],
    'MODALITY_1': [
        'modality_1', 'modality1', 'mod1', 'modality 1',
    ],
    'MODALITY_2': [
        'modality_2', 'modality2', 'mod2', 'modality 2',
    ],
    'ENHANCED_IMAGE': [
        'enhanced_image', 'enhancedimage', 'enhanced image', 'image', 'img',
    ],
    'VERIFICATION': [
        'verification', 'verif', 'status', 'verify', 'result',
        'classification', 'class',
    ],
}

# Minimum required columns for valid AOI data
REQUIRED_COLUMNS = {'DEFECT_TYPE', 'X_COORDINATES', 'Y_COORDINATES'}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AOILoadResult:
    """Result of loading a single AOI Excel file."""
    df: pd.DataFrame
    buildup: int
    side: str           # 'F' or 'B'
    source_file: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class AOIDataset:
    """Aggregated AOI defect data from multiple files."""
    all_defects: pd.DataFrame = field(default_factory=pd.DataFrame)
    defect_types: list[str] = field(default_factory=list)
    buildup_numbers: list[int] = field(default_factory=list)
    sides: list[str] = field(default_factory=list)
    panel_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return len(self.all_defects) > 0

    @property
    def coord_bounds(self) -> tuple[float, float, float, float]:
        """Return (minx, miny, maxx, maxy) in mm."""
        if not self.has_data:
            return (0, 0, 0, 0)
        df = self.all_defects
        return (
            df['X_MM'].min(),
            df['Y_MM'].min(),
            df['X_MM'].max(),
            df['Y_MM'].max(),
        )


# ---------------------------------------------------------------------------
# Column auto-detection
# ---------------------------------------------------------------------------

def _normalize_col_name(name: str) -> str:
    """Normalize a column name for alias matching: lowercase, strip, replace separators."""
    return re.sub(r'[\s_\-]+', '_', str(name).strip().lower())


def _auto_map_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    Auto-detect and rename DataFrame columns to canonical names.

    Returns:
        (renamed_df, list_of_warnings) — warnings list unmapped critical columns
    """
    # Build reverse lookup: normalized_alias → canonical_name
    alias_to_canonical = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            alias_to_canonical[_normalize_col_name(alias)] = canonical

    rename_map = {}
    mapped_canonical = set()

    for col in df.columns:
        normalized = _normalize_col_name(col)
        if normalized in alias_to_canonical:
            canonical = alias_to_canonical[normalized]
            if canonical not in mapped_canonical:
                rename_map[col] = canonical
                mapped_canonical.add(canonical)

    # Apply renames
    df = df.rename(columns=rename_map)

    # Check for missing required columns
    warnings = []
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        warnings.append(f"Missing required columns: {', '.join(sorted(missing))}")

    return df, warnings


def render_column_mapping_ui(df: pd.DataFrame) -> Optional[dict]:
    """
    Render a Streamlit UI for manual column mapping when auto-detection fails.

    Returns a mapping dict {original_column → canonical_name} or None if the
    user hasn't completed the mapping yet.
    """
    st.warning("Could not auto-detect all required columns. Please map them manually:")

    available_cols = ['(not mapped)'] + list(df.columns)
    mapping = {}

    cols = st.columns(3)
    for i, (canonical, description) in enumerate([
        ('DEFECT_TYPE', 'Defect Type'),
        ('X_COORDINATES', 'X Coordinate (microns)'),
        ('Y_COORDINATES', 'Y Coordinate (microns)'),
    ]):
        if canonical not in df.columns:
            with cols[i % 3]:
                selected = st.selectbox(
                    f"Map → {description}",
                    available_cols,
                    key=f"col_map_{canonical}"
                )
                if selected != '(not mapped)':
                    mapping[selected] = canonical

    # Optional columns
    with st.expander("Optional column mappings"):
        opt_cols = st.columns(3)
        for i, (canonical, description) in enumerate([
            ('DEFECT_ID', 'Defect ID'),
            ('UNIT_INDEX_X', 'Unit Index X'),
            ('UNIT_INDEX_Y', 'Unit Index Y'),
            ('VERIFICATION', 'Verification / Status'),
        ]):
            if canonical not in df.columns:
                with opt_cols[i % 3]:
                    selected = st.selectbox(
                        f"Map → {description}",
                        available_cols,
                        key=f"col_map_opt_{canonical}"
                    )
                    if selected != '(not mapped)':
                        mapping[selected] = canonical

    # Check if all required columns are mapped
    mapped_canonical = set(mapping.values()) | (set(df.columns) & REQUIRED_COLUMNS)
    if REQUIRED_COLUMNS.issubset(mapped_canonical):
        return mapping

    return None


# ---------------------------------------------------------------------------
# Buildup / Side / Panel / Section extraction from filename
# ---------------------------------------------------------------------------

def _parse_filename(filename: str) -> tuple[int, str, str, int, list[str]]:
    """
    Extract buildup number, side, panel ID and section from the filename.

    Supported formats:
      New:    BU_01F_Panel1_S2.xlsx  → buildup=1, side='F', panel='Panel_01', section=2
              BU_01F_Panel1.xlsx     → buildup=1, side='F', panel='Panel_01', section=1
      Legacy: BU-02F.xlsx            → buildup=2, side='F', panel='Panel_01', section=1

    Returns:
        (buildup_number, side_letter, panel_id, section_number, warnings)
    """
    warnings = []

    # ── New format: BU_01F_Panel1_S2 ─────────────────────────────────────
    m = FILENAME_PATTERN_NEW.search(filename)
    if m:
        buildup    = int(m.group(1))
        side       = m.group(2).upper()
        panel_id   = f"Panel_{int(m.group(3)):02d}"
        section    = int(m.group(4)) if m.group(4) else 1
        return (buildup, side, panel_id, section, warnings)

    # ── Legacy format: BU-02F ─────────────────────────────────────────────
    m = FILENAME_PATTERN_LEGACY.search(filename)
    if m:
        buildup  = int(m.group(1))
        side     = m.group(2).upper()
        warnings.append(
            f"'{filename}' uses legacy naming — panel defaulted to Panel_01. "
            f"Rename to BU_{int(m.group(1)):02d}{m.group(2).upper()}_Panel1_S1.xlsx for multi-panel support."
        )
        return (buildup, side, 'Panel_01', 1, warnings)

    # ── Fallback ──────────────────────────────────────────────────────────
    warnings.append(
        f"Could not parse buildup/side from '{filename}' — defaulting to BU-0, Front, Panel_01, S1."
    )
    return (0, 'F', 'Panel_01', 1, warnings)


# Keep legacy name as a thin wrapper so nothing else breaks
def _extract_buildup_side(filename: str) -> tuple[int, str, list[str]]:
    buildup, side, _panel, _section, warnings = _parse_filename(filename)
    return (buildup, side, warnings)


# ---------------------------------------------------------------------------
# Single file loader
# ---------------------------------------------------------------------------

def _load_single_aoi(
    file_bytes: bytes,
    filename: str,
    buildup: int,
    side: str,
    column_mapping: Optional[dict] = None,
    panel_id: str = 'Panel_01',
    section: int = 1,
) -> AOILoadResult:
    """
    Load a single AOI Excel file and return standardized data.

    Steps:
    1. Read Excel (try 'Defects' sheet first, fall back to first sheet)
    2. Auto-map columns to canonical names
    3. Apply manual column mapping if provided
    4. Convert X/Y from microns to mm
    5. Clean and validate data
    """
    warnings = []

    # Read Excel
    import io
    try:
        # Try 'Defects' sheet first (common in Orbotech exports)
        try:
            df = pd.read_excel(
                io.BytesIO(file_bytes), sheet_name='Defects', engine='openpyxl'
            )
        except (ValueError, KeyError):
            df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, engine='openpyxl')
    except Exception as e:
        return AOILoadResult(
            df=pd.DataFrame(),
            buildup=buildup, side=side,
            source_file=filename,
            warnings=[f"Failed to read Excel file: {e}"]
        )

    if df.empty:
        return AOILoadResult(
            df=df, buildup=buildup, side=side,
            source_file=filename,
            warnings=["Excel file is empty"]
        )

    # Auto-map columns
    df, map_warnings = _auto_map_columns(df)
    warnings.extend(map_warnings)

    # Apply manual column mapping if provided
    if column_mapping:
        df = df.rename(columns=column_mapping)

    # Check required columns
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        return AOILoadResult(
            df=df, buildup=buildup, side=side,
            source_file=filename,
            warnings=warnings + [f"Missing required columns after mapping: {', '.join(missing)}"]
        )

    # --- Data cleaning ---

    # DEFECT_TYPE: strip whitespace, convert to category
    df['DEFECT_TYPE'] = df['DEFECT_TYPE'].astype(str).str.strip()
    df['DEFECT_TYPE'] = df['DEFECT_TYPE'].astype('category')

    # Coordinates: ensure numeric, drop rows with NaN coordinates
    for col in ['X_COORDINATES', 'Y_COORDINATES']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    rows_before = len(df)
    df = df.dropna(subset=['X_COORDINATES', 'Y_COORDINATES'])
    rows_dropped = rows_before - len(df)
    if rows_dropped > 0:
        warnings.append(f"Dropped {rows_dropped} rows with invalid coordinates")

    # Convert microns → mm
    # AOI machines (Orbotech) report coordinates in microns from board edge
    df['X_MM'] = df['X_COORDINATES'] / 1000.0
    df['Y_MM'] = df['Y_COORDINATES'] / 1000.0

    # Add metadata columns
    df['BUILDUP'] = buildup
    df['SIDE'] = side
    df['SOURCE_FILE'] = filename
    df['PANEL_ID'] = panel_id
    df['SECTION'] = section

    # Optional column cleanup
    if 'DEFECT_ID' in df.columns:
        df['DEFECT_ID'] = pd.to_numeric(df['DEFECT_ID'], errors='coerce').fillna(-1).astype(int)

    if 'VERIFICATION' in df.columns:
        df['VERIFICATION'] = df['VERIFICATION'].astype(str).str.strip().str.upper()
        df['VERIFICATION'] = df['VERIFICATION'].fillna('N')

    if 'UNIT_INDEX_X' in df.columns:
        df['UNIT_INDEX_X'] = pd.to_numeric(df['UNIT_INDEX_X'], errors='coerce').fillna(0).astype(int)
    if 'UNIT_INDEX_Y' in df.columns:
        df['UNIT_INDEX_Y'] = pd.to_numeric(df['UNIT_INDEX_Y'], errors='coerce').fillna(0).astype(int)

    return AOILoadResult(
        df=df,
        buildup=buildup,
        side=side,
        source_file=filename,
        warnings=warnings
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_aoi_files(uploaded_files: list, classifications: Optional[list] = None) -> AOIDataset:
    """
    Load multiple AOI Excel files into a unified AOIDataset.

    If `classifications` is provided (list of dicts from the sidebar UI), each
    entry must contain: {'file', 'panel', 'buildup', 'side', 'section'}.
    Otherwise buildup/side/panel/section are parsed from the filename.

    Args:
        uploaded_files:  list of Streamlit UploadedFile objects
        classifications: optional list of per-file classification dicts from the UI

    Args:
        uploaded_files: list of Streamlit UploadedFile objects

    Returns:
        AOIDataset with all defects, available defect types, and buildup numbers
    """
    if not uploaded_files:
        return AOIDataset(warnings=["No AOI files uploaded"])

    # Cold load: parse from Excel
    all_results = []
    all_warnings = []

    # If classifications provided, it's a list of dicts with keys:
    # {'file', 'panel', 'buildup', 'side', 'section'} in the same order as uploaded_files
    for i, uf in enumerate(uploaded_files):
        filename = uf.name
        file_bytes = uf.read()
        uf.seek(0)  # reset for potential re-read

        # Prefer UI-provided classification when available
        if classifications and i < len(classifications) and classifications[i].get('file'):
            _cl = classifications[i]
            try:
                panel_id = f"Panel_{int(_cl.get('panel', 1)):02d}"
            except Exception:
                panel_id = 'Panel_01'
            try:
                buildup = int(_cl.get('buildup', 0))
            except Exception:
                buildup = 0
            side = 'F' if str(_cl.get('side', 'Front')).lower().startswith('f') else 'B'
            try:
                section = int(_cl.get('section', 1))
            except Exception:
                section = 1
        else:
            # Extract buildup, side, panel and section from filename
            buildup, side, panel_id, section, extract_warnings = _parse_filename(filename)
            all_warnings.extend(extract_warnings)

        # Load and process the file
        result = _load_single_aoi(file_bytes, filename, buildup, side,
                                  panel_id=panel_id, section=section)
        all_warnings.extend(result.warnings)

        if not result.df.empty:
            all_results.append(result)

    if not all_results:
        return AOIDataset(warnings=all_warnings + ["No valid defect data loaded"])

    # Concatenate all DataFrames
    all_dfs = [r.df for r in all_results]
    combined = pd.concat(all_dfs, ignore_index=True)

    # Extract unique values for filters
    defect_types = sorted(combined['DEFECT_TYPE'].unique().tolist())
    buildup_numbers = sorted(combined['BUILDUP'].unique().tolist())
    sides = sorted(combined['SIDE'].unique().tolist())
    panel_ids = sorted(combined['PANEL_ID'].unique().tolist()) if 'PANEL_ID' in combined.columns else []

    dataset = AOIDataset(
        all_defects=combined,
        defect_types=defect_types,
        buildup_numbers=buildup_numbers,
        sides=sides,
        panel_ids=panel_ids,
        warnings=all_warnings,
    )

    return dataset


def load_aoi_with_manual_side(
    uploaded_files: list,
    buildup_side_map: dict[str, tuple[int, str]]
) -> AOIDataset:
    """
    Load AOI files with manually specified buildup/side per file.

    Use this when filenames don't follow the BU-XXF/B convention.

    Args:
        uploaded_files: list of Streamlit UploadedFile objects
        buildup_side_map: dict mapping filename → (buildup_number, side)

    Returns:
        AOIDataset
    """
    all_bytes = []
    for uf in uploaded_files:
        b = uf.read()
        all_bytes.append((uf.name, b))
        uf.seek(0)

    all_results = []
    all_warnings = []

    for filename, file_bytes in all_bytes:
        buildup, side = buildup_side_map.get(filename, (0, 'F'))
        result = _load_single_aoi(file_bytes, filename, buildup, side)
        all_warnings.extend(result.warnings)
        if not result.df.empty:
            all_results.append(result)

    if not all_results:
        return AOIDataset(warnings=all_warnings + ["No valid defect data loaded"])

    combined = pd.concat([r.df for r in all_results], ignore_index=True)
    dataset = AOIDataset(
        all_defects=combined,
        defect_types=sorted(combined['DEFECT_TYPE'].unique().tolist()),
        buildup_numbers=sorted(combined['BUILDUP'].unique().tolist()),
        sides=sorted(combined['SIDE'].unique().tolist()),
        panel_ids=sorted(combined['PANEL_ID'].unique().tolist()) if 'PANEL_ID' in combined.columns else [],
        warnings=all_warnings
    )
    return dataset
