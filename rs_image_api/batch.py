"""
Batch workflow: shapefile/gpkg directory -> probe -> download.
Supports multiple backends (gehi, bing, google, esri).
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import geopandas as gpd
import requests

from .tile_system import meters_per_pixel
from .config import find_gehi_exe


@dataclass
class Task:
    name: str
    bbox: tuple  # (lng_min, lat_min, lng_max, lat_max)


@dataclass
class ProbedTask(Task):
    zoom: int = 19
    date: str = ""  # yyyy/MM/dd
    resolution_m: float = 0.0


def collect_tasks(source) -> list[Task]:
    """
    Collect download tasks from:
    - A directory of .gpkg/.shp files
    - A single .gpkg/.shp file
    - A bbox tuple
    """
    source = Path(source)
    tasks = []

    if source.is_dir():
        files = sorted(list(source.glob("*.gpkg")) + list(source.glob("*.shp")))
        for f in files:
            gdf = gpd.read_file(f)
            bounds = gdf.total_bounds
            tasks.append(Task(name=f.stem, bbox=tuple(bounds)))
    elif source.is_file():
        gdf = gpd.read_file(source)
        tasks.append(Task(name=source.stem, bbox=tuple(gdf.total_bounds)))

    return tasks


def probe_tasks(tasks: list[Task], backend: str = "gehi",
                max_zoom: int = 20, min_zoom: int = 18) -> list[ProbedTask]:
    """
    Probe each task to determine best zoom and latest date.

    backend: 'gehi' | 'bing' | 'google' | 'esri'
    """
    probed = []

    if backend == "gehi":
        from .gehi import gehi_find_best

        for task in tasks:
            lat = (task.bbox[1] + task.bbox[3]) / 2
            lng = (task.bbox[0] + task.bbox[2]) / 2

            result = gehi_find_best(lat, lng, max_zoom=max_zoom, min_zoom=min_zoom)
            zoom = result["best_zoom"]
            date = result["latest_date"] or ""
            res = meters_per_pixel(lat, zoom)

            probed.append(ProbedTask(
                name=task.name, bbox=task.bbox,
                zoom=zoom, date=date, resolution_m=round(res, 3)
            ))

    elif backend == "bing":
        from .providers import get_provider
        from .downloader import find_best_zoom, get_capture_date

        provider = get_provider("bing")
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        for task in tasks:
            lat = (task.bbox[1] + task.bbox[3]) / 2
            lng = (task.bbox[0] + task.bbox[2]) / 2

            zoom = find_best_zoom(provider, lat, lng, session)
            date_meta = get_capture_date(provider, lat, lng, zoom, session)
            year = date_meta.get("capture_year", "")
            res = meters_per_pixel(lat, zoom)

            probed.append(ProbedTask(
                name=task.name, bbox=task.bbox,
                zoom=zoom, date=year, resolution_m=round(res, 3)
            ))

    else:
        # Generic tile provider
        from .providers import get_provider
        from .downloader import find_best_zoom

        provider = get_provider(backend)
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        for task in tasks:
            lat = (task.bbox[1] + task.bbox[3]) / 2
            lng = (task.bbox[0] + task.bbox[2]) / 2
            zoom = find_best_zoom(provider, lat, lng, session)
            res = meters_per_pixel(lat, zoom)
            probed.append(ProbedTask(
                name=task.name, bbox=task.bbox,
                zoom=zoom, date="", resolution_m=round(res, 3)
            ))

    return probed


def save_plan(probed: list[ProbedTask], output_path: Path):
    """Save probed tasks as a CSV plan file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["village", "lat", "lng", "best_zoom", "latest_date", "resolution_m"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in probed:
            lat = (t.bbox[1] + t.bbox[3]) / 2
            lng = (t.bbox[0] + t.bbox[2]) / 2
            writer.writerow({
                "village": t.name,
                "lat": f"{lat:.6f}",
                "lng": f"{lng:.6f}",
                "best_zoom": t.zoom,
                "latest_date": t.date,
                "resolution_m": t.resolution_m,
            })


def download_tasks(probed: list[ProbedTask], backend: str,
                   output_dir: Path, buffer_deg: float = 0.0005,
                   parallel: int = 8) -> list[dict]:
    """
    Download imagery for all probed tasks.
    Skips existing files. Returns metadata dicts.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    if backend == "gehi":
        from .gehi import gehi_download

        for i, task in enumerate(probed, 1):
            date_compact = task.date.replace("/", "") if task.date else "unknown"
            filename = f"{task.name}_ge_z{task.zoom}_{date_compact}.tif"
            output_path = output_dir / filename

            if output_path.exists():
                print(f"[{i}/{len(probed)}] {task.name} - skip (exists)")
                results.append({"name": task.name, "filename": filename, "skipped": True})
                continue

            print(f"[{i}/{len(probed)}] {task.name} (z{task.zoom}, {task.date})")

            # Expand bbox
            lng_min, lat_min, lng_max, lat_max = task.bbox
            lng_min -= buffer_deg
            lat_min -= buffer_deg
            lng_max += buffer_deg
            lat_max += buffer_deg

            try:
                result = gehi_download(
                    bbox=(lng_min, lat_min, lng_max, lat_max),
                    zoom=task.zoom, date=task.date,
                    output_path=output_path, parallel=parallel,
                )
                if result["success"]:
                    print(f"  [OK] {filename} ({result['size_mb']:.1f} MB)")
                else:
                    print(f"  [FAIL] download returned but file missing/empty")
                results.append({**result, "name": task.name, "filename": filename})
            except Exception as e:
                print(f"  [ERROR] {e}")
                results.append({"name": task.name, "filename": filename, "success": False, "error": str(e)})

    else:
        # Tile-based download
        from .providers import get_provider
        from .downloader import download_bbox

        provider = get_provider(backend)

        for i, task in enumerate(probed, 1):
            date_str = task.date if task.date else ""
            if date_str and len(date_str) == 4:  # year only (bing)
                filename = f"{task.name}_{backend}_z{task.zoom}_{date_str}.tif"
            elif date_str:
                filename = f"{task.name}_{backend}_z{task.zoom}_{date_str.replace('/', '')}.tif"
            else:
                filename = f"{task.name}_{backend}_z{task.zoom}.tif"

            # Check existing
            existing = list(output_dir.glob(f"{task.name}_{backend}_z*.tif"))
            if existing:
                print(f"[{i}/{len(probed)}] {task.name} - skip (exists)")
                results.append({"name": task.name, "filename": filename, "skipped": True})
                continue

            print(f"[{i}/{len(probed)}] {task.name} (z{task.zoom})")

            try:
                output_path = output_dir / Path(filename).stem
                result = download_bbox(
                    provider=provider, bbox=task.bbox,
                    output_path=output_path, zoom=task.zoom,
                )
                print(f"  [OK] {result['width_px']}x{result['height_px']}px, {result['file_size_mb']:.1f}MB")
                results.append({**result, "name": task.name})
            except Exception as e:
                print(f"  [ERROR] {e}")
                results.append({"name": task.name, "filename": filename, "success": False, "error": str(e)})

    return results
