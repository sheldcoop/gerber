import hashlib
import json
import logging
import os
import pickle
from pathlib import Path
from typing import Optional, Any

import pandas as pd

logger = logging.getLogger(__name__)

CACHE_VERSION = "1.0.0"
CACHE_DIR = Path.home() / '.cache' / 'gerber-vrs'
CAM_CACHE_DIR = CACHE_DIR / 'cam'

def init_cache_dirs():
    """Ensure cache directories exist."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CAM_CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _get_file_hash(data: bytes) -> str:
    """Return MD5 hash of byte data."""
    return hashlib.md5(data).hexdigest()

def get_cam_cache_path(tgz_bytes: bytes) -> Path:
    return CAM_CACHE_DIR / f"{_get_file_hash(tgz_bytes)}.pkl"

def save_render_cache(tgz_bytes: bytes, payload: dict) -> None:
    """Save parsed ODB++ payload to cache using pickle."""
    init_cache_dirs()
    cache_path = get_cam_cache_path(tgz_bytes)

    # Wrap payload with version
    cache_data = {
        'version': CACHE_VERSION,
        'payload': payload
    }

    try:
        with open(cache_path, 'wb') as f:
            pickle.dump(cache_data, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        logger.warning(f"Failed to write CAM cache: {e}")

def load_render_cache(tgz_bytes: bytes) -> Optional[dict]:
    """Load parsed ODB++ payload from cache. Returns None if miss or version mismatch."""
    cache_path = get_cam_cache_path(tgz_bytes)
    if not cache_path.exists():
        return None

    try:
        with open(cache_path, 'rb') as f:
            cache_data = pickle.load(f)

        if cache_data.get('version') != CACHE_VERSION:
            logger.info("CAM cache version mismatch, invalidating.")
            cache_path.unlink(missing_ok=True)
            return None

        return cache_data.get('payload')
    except Exception as e:
        logger.warning(f"Failed to read CAM cache: {e}")
        return None

def get_aoi_cache_paths(file_hash: str) -> tuple[Path, Path]:
    return CACHE_DIR / f"{file_hash}.parquet", CACHE_DIR / f"{file_hash}.meta"

def save_aoi_cache(file_hash: str, df: pd.DataFrame, meta: dict) -> None:
    """Save AOI dataframe and metadata to cache."""
    init_cache_dirs()
    parquet_path, meta_path = get_aoi_cache_paths(file_hash)

    meta_with_version = {
        'version': CACHE_VERSION,
        **meta
    }

    try:
        # Convert categorical to string for Parquet compatibility
        df_copy = df.copy()
        if 'DEFECT_TYPE' in df_copy.columns:
            df_copy['DEFECT_TYPE'] = df_copy['DEFECT_TYPE'].astype(str)
        df_copy.to_parquet(parquet_path, engine='pyarrow', index=False)

        meta_path.write_text(json.dumps(meta_with_version))
        logger.info(f"Cached AOI data to {parquet_path}")
    except Exception as e:
        logger.warning(f"Failed to write AOI cache: {e}")

def load_aoi_cache(file_hash: str) -> Optional[tuple[pd.DataFrame, dict]]:
    """Load AOI dataframe and metadata from cache. Returns None if miss or version mismatch."""
    parquet_path, meta_path = get_aoi_cache_paths(file_hash)
    if not parquet_path.exists() or not meta_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text())
        if meta.get('version') != CACHE_VERSION:
            logger.info("AOI cache version mismatch, invalidating.")
            parquet_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            return None

        df = pd.read_parquet(parquet_path)
        if 'DEFECT_TYPE' in df.columns:
            df['DEFECT_TYPE'] = df['DEFECT_TYPE'].astype('category')

        return df, meta
    except Exception as e:
        logger.warning(f"Failed to read AOI cache: {e}")
        return None

def clear_all_caches():
    """Clear all application caches."""
    import shutil
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
        init_cache_dirs()
