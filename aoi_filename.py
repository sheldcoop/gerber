"""aoi_filename.py — AOI filename parsing (buildup / side / panel / section)."""

import re
from typing import Optional

FILENAME_PATTERN_NEW = re.compile(
    r'BU[_\-]?(\d{1,2})\s*([FfBb])[_\-]Panel[_\-]?(\d+)(?:[_\-]S?(\d+))?',
    re.IGNORECASE
)
FILENAME_PATTERN_LEGACY = re.compile(r'BU[-_]?(\d{1,2})\s*([FfBb])', re.IGNORECASE)
# Backward-compatible alias — sidebar.py and tests still import this name
FILENAME_PATTERN = FILENAME_PATTERN_LEGACY


def _parse_filename(filename: str) -> tuple[int, str, str, int, list[str]]:
    """
    Extract buildup number, side, panel ID and section from the filename.

    Supported formats:
      New:    BU_01F_Panel1_S2.xlsx      → buildup=1, side='F', panel='Panel_01', section=2
              BU_01F_Panel1.xlsx         → buildup=1, side='F', panel='Panel_01', section=1
              BU-01B-Panel-30-1.xlsx     → buildup=1, side='B', panel='Panel_30', section=1
              BU-01B-Panel-30.xlsx       → buildup=1, side='B', panel='Panel_30', section=1
      Legacy: BU-02F.xlsx                → buildup=2, side='F', panel='Panel_01', section=1

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

