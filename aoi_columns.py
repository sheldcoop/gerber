"""aoi_columns.py — column-name aliasing, auto-mapping and the mapping UI."""

import re
from typing import Optional

import pandas as pd
import streamlit as st


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

