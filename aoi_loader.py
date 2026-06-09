"""
aoi_loader.py — AOI (Automated Optical Inspection) defect data loader.

Loads Orbotech AOI defect data from Excel files, auto-detects column mappings,
extracts buildup layer, side (Front/Back), panel number and section from filenames,
and converts coordinates from microns to mm.

Filename convention (new format):
  BU_01F_Panel1_S1.xlsx      → Buildup 1, Front, Panel 1,  Section 1
  BU_02B_Panel2_S3.xlsx      → Buildup 2, Back,  Panel 2,  Section 3
  BU_01F_Panel1.xlsx         → Buildup 1, Front, Panel 1,  Section 1 (section optional)
  BU-01B-Panel-30-1.xlsx     → Buildup 1, Back,  Panel 30, Section 1
  BU-01B-Panel-30.xlsx       → Buildup 1, Back,  Panel 30, Section 1 (section optional)

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


# ── Re-exported from the split modules (keeps `from aoi_loader import …` working) ──
from aoi_models import AOILoadResult, AOIDataset
from aoi_columns import (
    COLUMN_ALIASES, REQUIRED_COLUMNS,
    _normalize_col_name, _auto_map_columns, render_column_mapping_ui,
)
from aoi_filename import (
    FILENAME_PATTERN_NEW, FILENAME_PATTERN_LEGACY, FILENAME_PATTERN,
    _parse_filename, _extract_buildup_side,
)


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

    # Read Excel — try polars first (3-10× faster), fallback to pandas
    import io
    df = None
    try:
        # Try polars for faster Excel reading if available
        try:
            import polars as pl
            # Try 'Defects' sheet first (common in Orbotech exports)
            try:
                pl_df = pl.read_excel(file_bytes, sheet_name='Defects')
            except Exception:
                pl_df = pl.read_excel(file_bytes, sheet_id=0)
            df = pl_df.to_pandas()  # Convert to pandas for compatibility
        except (ImportError, Exception):
            # Polars not available or failed — use pandas
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
        # Convert to string, strip, uppercase — but first replace actual NaN / None
        # with a placeholder BEFORE .astype(str), otherwise NaN becomes the string
        # 'nan' → 'NAN' which pollutes the legend with a spurious 'NAN' group.
        df['VERIFICATION'] = (
            df['VERIFICATION']
            .where(df['VERIFICATION'].notna() & (df['VERIFICATION'].astype(str).str.strip() != ''), other='—')
            .astype(str).str.strip().str.upper()
        )
        # Normalise the sentinel that slipped through .astype(str) for real NaN rows.
        df['VERIFICATION'] = df['VERIFICATION'].replace({'NAN': '—', 'NONE': '—', '': '—'})

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
