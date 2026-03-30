import logging
from typing import Optional, List, Dict, Any

from aoi_loader import load_aoi_files, load_aoi_with_manual_side, AOIDataset

logger = logging.getLogger(__name__)

class AOIService:
    """Encapsulates AOI loading and filtering logic."""

    @staticmethod
    def load_files(files: List[Any], manual_map: Optional[Dict[str, tuple]] = None) -> Optional[AOIDataset]:
        """Load multiple AOI files, optionally overriding buildup/side detection."""
        try:
            if manual_map:
                logger.info("Loading AOI with manual side mapping.")
                return load_aoi_with_manual_side(files, manual_map)
            else:
                logger.info("Loading AOI files with auto-detection.")
                return load_aoi_files(files)
        except Exception as e:
            logger.error(f"Failed to load AOI files: {e}")
            raise

    @staticmethod
    def get_initial_filter_state(dataset: AOIDataset) -> Dict[str, Any]:
        """Get the default filter state based on loaded dataset."""
        if not dataset or not dataset.has_data:
            return {'buildup_numbers': [], 'sides': []}

        return {
            'buildup_numbers': list(dataset.buildup_numbers),
            'sides': ['Front', 'Back'] if 'F' in dataset.sides or 'B' in dataset.sides else list(dataset.sides)
        }
