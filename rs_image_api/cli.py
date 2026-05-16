"""
CLI interface for RS-image-api
Usage:
    python -m rs_image_api download --provider google --bbox 83.59,28.55,84.03,29.24 --zoom 19 -o output
    python -m rs_image_api download --provider bing --shapefile regions.gpkg --zoom auto -o ./output_dir
    python -m rs_image_api probe --provider google --lat 28.78 --lng 83.72
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import geopandas as gpd

from .providers import get_provider, PROVIDERS
from .downloader import download_bbox, find_best_zoom
from .tile_system import meters_per_pixel
import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}


def cmd_probe(args):
    """探测某坐标各 provider 的最高可用 zoom 和日期"""
    print(f"\n探测坐标: ({args.lat}, {args.lng})")
    print("=" * 60)

    providers_to_check = [args.provider] if args.provider != 'all' else list(PROVIDERS.keys())

    session = requests.Session()
    session.headers.update(HEADERS)

    for pname in providers_to_check:
        try:
            provider = get_provider(pname)
        except Exception:
            continue

        best_zoom = find_best_zoom(provider, args.lat, args.lng, session)
        res = meters_per_pixel(args.lat, best_zoom)

        # 获取日期
        from .downloader import get_capture_date
        date_meta = get_capture_date(provider, args.lat, args.lng, best_zoom, session)

        print(f"\n  [{pname.upper()}]")
        print(f"    最高Zoom: {best_zoom}")
        print(f"    分辨率: {res:.3f} m/pixel")
        if date_meta:
            for k, v in date_meta.items():
                if v:
                    print(f"    {k}: {v}")

    print()


def cmd_download(args):
    """下载影像"""
    provider = get_provider(args.provider)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    zoom = None if args.zoom == 'auto' else int(args.zoom)

    # 收集所有 bbox
    tasks = []

    if args.bbox:
        parts = [float(x) for x in args.bbox.split(',')]
        bbox = tuple(parts)  # lng_min, lat_min, lng_max, lat_max
        tasks.append(('custom_bbox', bbox))

    elif args.shapefile:
        shp_path = Path(args.shapefile)
        if shp_path.is_dir():
            # 目录模式：遍历所有 gpkg/shp
            files = sorted(list(shp_path.glob('*.gpkg')) + list(shp_path.glob('*.shp')))
        else:
            files = [shp_path]

        for f in files:
            gdf = gpd.read_file(f)
            bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
            name = f.stem
            tasks.append((name, tuple(bounds)))

    else:
        print("错误: 需要 --bbox 或 --shapefile 参数")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"RS-image-api 影像下载")
    print(f"  Provider: {provider.name}")
    print(f"  Zoom: {zoom or 'auto'}")
    print(f"  任务数: {len(tasks)}")
    print(f"  输出: {output_dir}")
    print(f"{'=' * 60}\n")

    # CSV 记录
    csv_path = output_dir / 'metadata.csv'
    csv_rows = []

    success = 0
    fail = 0

    for i, (name, bbox) in enumerate(tasks, 1):
        # 检查已存在
        existing = list(output_dir.glob(f"{name}_{provider.name}_z*.tif"))
        if existing:
            print(f"[{i}/{len(tasks)}] {name} - 跳过 (已存在)")
            success += 1
            continue

        print(f"[{i}/{len(tasks)}] {name}...")

        try:
            # 确定输出文件名（下载后再根据 zoom 和年份重命名）
            temp_name = f"{name}_{provider.name}_temp"
            temp_path = output_dir / temp_name

            result = download_bbox(
                provider=provider,
                bbox=bbox,
                output_path=temp_path,
                zoom=zoom,
            )

            # 重命名加上 zoom 和年份
            year = result.get('capture_year', '')
            actual_zoom = result['zoom']
            final_name = f"{name}_{provider.name}_z{actual_zoom}"
            if year:
                final_name += f"_{year}"

            # 重命名文件
            for suffix in ['.tif', '.tfw', '.prj']:
                src = (output_dir / temp_name).with_suffix(suffix)
                dst = (output_dir / final_name).with_suffix(suffix)
                if src.exists():
                    src.rename(dst)

            result['filename'] = f"{final_name}.tif"
            result['name'] = name
            csv_rows.append(result)
            success += 1

            res_str = f"{result['resolution_m']:.2f}m/px"
            size_str = f"{result['file_size_mb']:.1f}MB"
            date_str = f", {year}" if year else ""
            print(f"  [OK] {result['width_px']}x{result['height_px']}px, z{actual_zoom}, "
                  f"{res_str}, {size_str}{date_str}")

        except Exception as e:
            print(f"  [FAIL] Error: {e}")
            fail += 1

    # 写 CSV
    if csv_rows:
        fieldnames = list(csv_rows[0].keys())
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)

    print(f"\n{'=' * 60}")
    print(f"完成! 成功: {success}, 失败: {fail}")
    if csv_rows:
        print(f"元数据: {csv_path}")
    print(f"输出目录: {output_dir}")


def main():
    parser = argparse.ArgumentParser(
        description='RS-image-api: Multi-source Remote Sensing Imagery Downloader'
    )
    subparsers = parser.add_subparsers(dest='command')

    # probe 子命令
    probe_parser = subparsers.add_parser('probe', help='探测可用影像')
    probe_parser.add_argument('--lat', type=float, required=True)
    probe_parser.add_argument('--lng', type=float, required=True)
    probe_parser.add_argument('--provider', default='all',
                              choices=['all'] + list(PROVIDERS.keys()))

    # download 子命令
    dl_parser = subparsers.add_parser('download', help='下载影像')
    dl_parser.add_argument('--provider', required=True, choices=list(PROVIDERS.keys()))
    dl_parser.add_argument('--bbox', help='lng_min,lat_min,lng_max,lat_max')
    dl_parser.add_argument('--shapefile', help='Shapefile/GeoPackage 路径或目录')
    dl_parser.add_argument('--zoom', default='auto', help='Zoom level (auto/数字)')
    dl_parser.add_argument('-o', '--output', default='./output', help='输出目录')

    args = parser.parse_args()

    if args.command == 'probe':
        cmd_probe(args)
    elif args.command == 'download':
        cmd_download(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
