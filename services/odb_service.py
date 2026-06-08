import logging
from typing import Optional, Dict, Any, Tuple
from pathlib import Path

from odb_parser import parse_odb_archive, ParsedODB, InvalidODBArchiveError
from gerber_renderer import render_odb_to_cam, RenderedODB, load_render_cache, save_render_cache

logger = logging.getLogger(__name__)

class ODBService:
    """Encapsulates ODB++ parsing and rendering logic."""

    @staticmethod
    def parse_archive(tgz_bytes: bytes, filename: str) -> Optional[ParsedODB]:
        """Parse ODB++ archive structure."""
        try:
            return parse_odb_archive(tgz_bytes, filename)
        except InvalidODBArchiveError as e:
            logger.error(f"Invalid ODB++ archive: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to parse ODB++ archive: {e}")
            raise

    @staticmethod
    def render_to_cam(tgz_bytes: bytes, filename: str) -> Optional[RenderedODB]:
        """Render parsed ODB to CAM-quality SVGs. Checks cache first."""
        cached = load_render_cache(tgz_bytes)
        if cached:
            logger.info("Loaded ODB++ render from cache.")
            return cached

        logger.info("Rendering ODB++ to CAM (cache miss).")
        rendered = render_odb_to_cam(tgz_bytes, filename)
        save_render_cache(tgz_bytes, rendered)
        return rendered

    @staticmethod
    def get_layer_visibility_defaults(rendered_odb: RenderedODB) -> Dict[str, bool]:
        """Determine default visibility for copper and drill layers."""
        visibility = {}
        for i, (name, lyr) in enumerate(rendered_odb.layers.items()):
            if lyr.layer_type in ('copper', 'soldermask'):
                visibility[name] = (i == 0) # Only outermost copper visible
            elif lyr.layer_type == 'drill':
                visibility[name] = False # Drill hidden by default
            else:
                visibility[name] = False
        return visibility
