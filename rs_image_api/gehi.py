"""
GEHistoricalImagery subprocess wrapper.
Provides Python interface to Google Earth historical imagery dates and downloads.

Requires GEHistoricalImagery CLI tool:
  https://github.com/Mbucari/GEHistoricalImagery
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from .config import find_gehi_exe, get_proj_lib


class GEHIError(Exception):
    """Error from GEHistoricalImagery CLI."""
    def __init__(self, cmd, returncode, stderr):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"GEHistoricalImagery failed (rc={returncode}): {stderr[:200]}")


def _get_env():
    """Build environment with PROJ_LIB set."""
    env = os.environ.copy()
    proj_lib = get_proj_lib()
    if proj_lib:
        env["PROJ_LIB"] = str(proj_lib)
    return env


def _run(args: list, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run GEHistoricalImagery command."""
    exe = find_gehi_exe()
    if not exe:
        raise FileNotFoundError(
            "GEHistoricalImagery not found. Download from: "
            "https://github.com/Mbucari/GEHistoricalImagery/releases"
        )

    cmd = [str(exe)] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, env=_get_env()
    )

    if result.returncode != 0 and "error" in result.stderr.lower():
        raise GEHIError(cmd, result.returncode, result.stderr)

    return result


def gehi_info(lat: float, lng: float, zoom: int = None,
              provider: str = "TM") -> dict:
    """
    Query imagery info at a location.

    Returns:
        {
            'zoom': int,
            'dates': ['2020/05/22', '2023/11/06', ...],
            'latest_date': '2023/11/06',
            'count': 5
        }
    """
    args = ["info", "-l", f"{lat:.6f},{lng:.6f}"]
    if zoom is not None:
        args.extend(["-z", str(zoom)])
    args.extend(["--provider", provider])

    result = _run(args, timeout=30)

    dates = re.findall(r"date = (\d{4}/\d{2}/\d{2})", result.stdout)
    zoom_match = re.search(r"Level = (\d+)", result.stdout)
    actual_zoom = int(zoom_match.group(1)) if zoom_match else zoom

    return {
        "zoom": actual_zoom,
        "dates": dates,
        "latest_date": dates[-1] if dates else None,
        "count": len(dates),
    }


def gehi_find_best(lat: float, lng: float, max_zoom: int = 20,
                   min_zoom: int = 18, provider: str = "TM") -> dict:
    """
    Find the highest zoom level with available imagery and its latest date.

    Returns:
        {'best_zoom': 20, 'latest_date': '2025/02/03', 'all_dates': [...]}
    """
    for z in range(max_zoom, min_zoom - 1, -1):
        info = gehi_info(lat, lng, zoom=z, provider=provider)
        if info["count"] > 0:
            return {
                "best_zoom": z,
                "latest_date": info["latest_date"],
                "all_dates": info["dates"],
            }

    return {"best_zoom": min_zoom, "latest_date": None, "all_dates": []}


def gehi_download(bbox: tuple, zoom: int, date: str, output_path: Path,
                  parallel: int = 8, provider: str = "TM",
                  timeout: int = 600) -> dict:
    """
    Download historical imagery for a bounding box.

    Args:
        bbox: (lng_min, lat_min, lng_max, lat_max)
        zoom: zoom level
        date: format 'yyyy/MM/dd'
        output_path: output .tif path
        parallel: concurrent download threads
        provider: 'TM' (Google Earth) or 'Wayback' (Esri)

    Returns:
        {'success': bool, 'path': Path, 'size_mb': float, 'tiles_total': int, 'tiles_ok': int}
    """
    lng_min, lat_min, lng_max, lat_max = bbox

    args = [
        "download",
        "--lower-left", f"{lat_min:.6f},{lng_min:.6f}",
        "--upper-right", f"{lat_max:.6f},{lng_max:.6f}",
        "-z", str(zoom),
        "-d", date,
        "-o", str(output_path),
        "-p", str(parallel),
        "--provider", provider,
    ]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = _run(args, timeout=timeout)

    # Parse tile count from output
    tiles_match = re.search(r"(\d+) out of (\d+) downloaded", result.stdout)
    tiles_ok = int(tiles_match.group(1)) if tiles_match else 0
    tiles_total = int(tiles_match.group(2)) if tiles_match else 0

    success = output_path.exists() and output_path.stat().st_size > 1000
    size_mb = output_path.stat().st_size / (1024 * 1024) if success else 0

    return {
        "success": success,
        "path": output_path,
        "size_mb": round(size_mb, 2),
        "tiles_total": tiles_total,
        "tiles_ok": tiles_ok,
    }
