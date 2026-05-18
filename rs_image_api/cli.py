"""
CLI interface for RS-image-api

Usage:
    rs-image probe   --lat 28.78 --lng 83.72 [--backend gehi|bing|all]
    rs-image batch   --input ./gpkg_dir/ --backend gehi -o ./output/
    rs-image merge   --input ./output/ -o merged.vrt
    rs-image info
    rs-image download --provider google --bbox lng_min,lat_min,lng_max,lat_max -o ./output/
"""

import argparse
import sys
from pathlib import Path

from .providers import PROVIDERS


def cmd_info(args):
    """Show detected tools and configuration."""
    from .config import find_gehi_exe, get_proj_lib

    print("RS-image-api Configuration")
    print("=" * 50)

    gehi = find_gehi_exe()
    print(f"  GEHistoricalImagery: {gehi or 'NOT FOUND'}")

    proj = get_proj_lib()
    print(f"  PROJ_LIB: {proj or 'NOT SET'}")

    try:
        import rasterio
        print(f"  rasterio: {rasterio.__version__}")
    except ImportError:
        print("  rasterio: not installed (worldfile fallback)")

    try:
        import geopandas
        print(f"  geopandas: {geopandas.__version__}")
    except ImportError:
        print("  geopandas: not installed")

    print(f"\n  Tile providers: {', '.join(PROVIDERS.keys())}")
    print(f"  Backends: gehi (Google Earth Historical), bing, google, esri")


def cmd_probe(args):
    """Probe imagery availability at a location."""
    import requests
    from .tile_system import meters_per_pixel

    print(f"\nProbing ({args.lat}, {args.lng})")
    print("=" * 60)

    backends = [args.backend] if args.backend != "all" else ["gehi", "bing", "google", "esri"]

    for backend in backends:
        try:
            if backend == "gehi":
                from .gehi import gehi_find_best
                result = gehi_find_best(args.lat, args.lng)
                res = meters_per_pixel(args.lat, result["best_zoom"])
                print(f"\n  [GE Historical]")
                print(f"    Best zoom: {result['best_zoom']} ({res:.3f} m/px)")
                print(f"    Latest date: {result['latest_date'] or 'N/A'}")
                print(f"    Available dates: {len(result['all_dates'])}")
                if result["all_dates"]:
                    for d in result["all_dates"][-5:]:
                        print(f"      {d}")

            elif backend in PROVIDERS:
                from .providers import get_provider
                from .downloader import find_best_zoom, get_capture_date

                provider = get_provider(backend)
                session = requests.Session()
                session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

                zoom = find_best_zoom(provider, args.lat, args.lng, session)
                res = meters_per_pixel(args.lat, zoom)
                date_meta = get_capture_date(provider, args.lat, args.lng, zoom, session)

                print(f"\n  [{backend.upper()}]")
                print(f"    Best zoom: {zoom} ({res:.3f} m/px)")
                for k, v in date_meta.items():
                    if v:
                        print(f"    {k}: {v}")

        except FileNotFoundError as e:
            print(f"\n  [{backend.upper()}] {e}")
        except Exception as e:
            print(f"\n  [{backend.upper()}] Error: {e}")


def cmd_batch(args):
    """Batch probe + download from shapefile/gpkg directory."""
    from .batch import collect_tasks, probe_tasks, save_plan, download_tasks

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: Collect
    tasks = collect_tasks(args.input)
    print(f"\n{'=' * 60}")
    print(f"RS-image-api Batch Download")
    print(f"  Backend: {args.backend}")
    print(f"  Input: {args.input} ({len(tasks)} regions)")
    print(f"  Output: {output_dir}")
    print(f"{'=' * 60}\n")

    # Phase 2: Probe
    print("Phase 1/2: Probing best zoom + date...")
    probed = probe_tasks(tasks, backend=args.backend)

    plan_path = output_dir / "download_plan.csv"
    save_plan(probed, plan_path)
    print(f"  Plan saved: {plan_path}")

    # Summary
    zooms = {}
    for t in probed:
        zooms[t.zoom] = zooms.get(t.zoom, 0) + 1
    print(f"  Zoom distribution: {zooms}")

    if args.probe_only:
        print("\n--probe-only: stopping after probe phase")
        return

    # Phase 3: Download
    print(f"\nPhase 2/2: Downloading {len(probed)} regions...")
    results = download_tasks(probed, backend=args.backend, output_dir=output_dir)

    success = sum(1 for r in results if r.get("success", False) or r.get("skipped", False))
    fail = len(results) - success
    print(f"\nDone! Success: {success}, Failed: {fail}")

    # Auto-generate VRT
    if success > 0:
        from .merge import generate_vrt
        try:
            vrt_path = generate_vrt(output_dir)
            print(f"VRT generated: {vrt_path}")
        except Exception as e:
            print(f"VRT generation skipped: {e}")


def cmd_merge(args):
    """Generate VRT or merge TIFFs."""
    from .merge import generate_vrt

    input_dir = Path(args.input)
    output_path = Path(args.output) if args.output else None

    vrt_path = generate_vrt(input_dir, output_path, pattern=args.pattern)
    print(f"VRT generated: {vrt_path}")


def cmd_download(args):
    """Download imagery for a single bbox (tile-based)."""
    from .providers import get_provider
    from .downloader import download_bbox

    provider = get_provider(args.provider)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    zoom = None if args.zoom == "auto" else int(args.zoom)

    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        bbox = tuple(parts)
    else:
        print("Error: --bbox required")
        sys.exit(1)

    output_path = output_dir / f"download_{provider.name}"

    result = download_bbox(
        provider=provider, bbox=bbox,
        output_path=output_path, zoom=zoom,
    )

    print(f"  {result['width_px']}x{result['height_px']}px, z{result['zoom']}, "
          f"{result['resolution_m']:.2f}m/px, {result['file_size_mb']:.1f}MB")


def main():
    parser = argparse.ArgumentParser(
        prog="rs-image",
        description="RS-image-api: Multi-source Remote Sensing Imagery Downloader",
    )
    subparsers = parser.add_subparsers(dest="command")

    # info
    subparsers.add_parser("info", help="Show detected tools and configuration")

    # probe
    probe_p = subparsers.add_parser("probe", help="Check imagery availability")
    probe_p.add_argument("--lat", type=float, required=True)
    probe_p.add_argument("--lng", type=float, required=True)
    probe_p.add_argument("--backend", default="all",
                         choices=["all", "gehi", "bing", "google", "esri"])

    # batch
    batch_p = subparsers.add_parser("batch", help="Batch probe + download")
    batch_p.add_argument("--input", "-i", required=True, help="Shapefile/GeoPackage path or directory")
    batch_p.add_argument("--backend", "-b", default="gehi",
                         choices=["gehi", "bing", "google", "esri"])
    batch_p.add_argument("--output", "-o", default="./output", help="Output directory")
    batch_p.add_argument("--probe-only", action="store_true", help="Only probe, don't download")

    # merge
    merge_p = subparsers.add_parser("merge", help="Generate VRT from downloaded TIFFs")
    merge_p.add_argument("--input", "-i", required=True, help="Directory containing TIFFs")
    merge_p.add_argument("--output", "-o", help="Output VRT path")
    merge_p.add_argument("--pattern", default="*.tif", help="Glob pattern for TIFFs")

    # download (single bbox, tile-based)
    dl_p = subparsers.add_parser("download", help="Download single bbox (tile-based)")
    dl_p.add_argument("--provider", required=True, choices=list(PROVIDERS.keys()))
    dl_p.add_argument("--bbox", help="lng_min,lat_min,lng_max,lat_max")
    dl_p.add_argument("--zoom", default="auto")
    dl_p.add_argument("-o", "--output", default="./output")

    args = parser.parse_args()

    if args.command == "info":
        cmd_info(args)
    elif args.command == "probe":
        cmd_probe(args)
    elif args.command == "batch":
        cmd_batch(args)
    elif args.command == "merge":
        cmd_merge(args)
    elif args.command == "download":
        cmd_download(args)
    else:
        parser.print_help()
