"""
Platform configuration: PROJ_LIB, encoding, PIL limits, tool discovery.
Auto-runs on package import.
"""

import os
import sys
from pathlib import Path
from typing import Optional

# Known locations for GEHistoricalImagery
_GEHI_SEARCH_PATHS = [
    Path("D:/tools/gehi2/gdal/GEHistoricalImagery.exe"),
    Path("D:/tools/GEHistoricalImagery/GEHistoricalImagery.exe"),
]

# Known locations for proj.db
_PROJ_SEARCH_PATHS = [
    Path("D:/tools/gehi2/gdal"),
    Path(os.environ.get("APPDATA", "") + "/Python/Python313/site-packages/rasterio/proj_data"),
    Path(os.environ.get("APPDATA", "") + "/Python/Python313/site-packages/pyproj/proj_dir/share/proj"),
]


def find_gehi_exe() -> Optional[Path]:
    """Find GEHistoricalImagery executable."""
    # Check env var first
    env_path = os.environ.get("GEHI_EXE")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    # Check PATH
    import shutil
    which = shutil.which("GEHistoricalImagery")
    if which:
        return Path(which)

    # Check known locations
    for p in _GEHI_SEARCH_PATHS:
        if p.exists():
            return p

    return None


def get_proj_lib() -> Optional[Path]:
    """Find PROJ_LIB directory containing proj.db."""
    # Already set
    existing = os.environ.get("PROJ_LIB")
    if existing and Path(existing, "proj.db").exists():
        return Path(existing)

    # Search known locations
    for p in _PROJ_SEARCH_PATHS:
        if p.exists() and (p / "proj.db").exists():
            return p

    return None


def configure_pil(max_pixels: int = 1_000_000_000):
    """Set PIL pixel limit to allow large satellite images."""
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = max_pixels
    except ImportError:
        pass


def setup():
    """Run all platform configuration. Called once on package import."""
    # Fix Windows GBK encoding
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, OSError):
            pass

    # Set PROJ_LIB if not already configured
    if not os.environ.get("PROJ_LIB"):
        proj_lib = get_proj_lib()
        if proj_lib:
            os.environ["PROJ_LIB"] = str(proj_lib)

    # Configure PIL
    configure_pil()
