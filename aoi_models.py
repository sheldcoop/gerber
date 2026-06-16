"""aoi_models.py — AOI dataclasses (load result + dataset)."""

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


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

