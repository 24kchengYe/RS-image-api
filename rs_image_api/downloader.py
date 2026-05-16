"""
Core downloader: fetches tiles, stitches, crops, and saves as GeoTIFF.
"""

import time
from pathlib import Path

import requests
from PIL import Image

# 解除 PIL 像素数限制 (大区域高zoom下会超过默认限制)
Image.MAX_IMAGE_PIXELS = None

from .tile_system import (
    lat_lng_to_pixel, pixel_to_lat_lng, pixel_to_tile,
    bbox_to_tiles, meters_per_pixel, TILE_SIZE
)
from .providers import TileProvider, HEADERS, REQUEST_DELAY

# WGS-84 PRJ string
WGS84_PRJ = 'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'


def find_best_zoom(provider: TileProvider, lat: float, lng: float, session: requests.Session) -> int:
    """从最高 zoom 开始，找到有有效影像的最高级别"""
    for zoom in range(provider.max_zoom, provider.min_zoom - 1, -1):
        px, py = lat_lng_to_pixel(lat, lng, zoom)
        tx, ty = pixel_to_tile(px, py)
        img, _ = provider.download_tile(tx, ty, zoom, session)
        if img is not None:
            return zoom
    return provider.min_zoom


def get_capture_date(provider: TileProvider, lat: float, lng: float, zoom: int, session: requests.Session) -> dict:
    """获取影像日期元数据（部分 provider 支持）"""
    # Bing 在 zoom 19 以下有日期头
    if provider.name == 'bing' and zoom >= 20:
        # fallback 到 zoom 19 获取日期
        check_zoom = 19
    else:
        check_zoom = zoom

    px, py = lat_lng_to_pixel(lat, lng, check_zoom)
    tx, ty = pixel_to_tile(px, py)
    url = provider.get_tile_url(tx, ty, check_zoom)
    try:
        resp = session.get(url, timeout=10)
        return provider.get_metadata(resp)
    except Exception:
        return {}


def save_with_worldfile(canvas: Image.Image, output_path: Path,
                        lng_min: float, lat_min: float,
                        lng_max: float, lat_max: float) -> Path:
    """保存为 TIFF + TFW + PRJ"""
    width, height = canvas.size

    x_pixel_size = (lng_max - lng_min) / width
    y_pixel_size = -(lat_max - lat_min) / height
    x_origin = lng_min + x_pixel_size / 2
    y_origin = lat_max + y_pixel_size / 2

    # 保存 TIFF
    tif_path = output_path.with_suffix('.tif')
    if width * height > 20_000_000:
        canvas.save(str(tif_path), format='TIFF')
    else:
        canvas.save(str(tif_path), format='TIFF', compression='tiff_deflate')

    # 保存 TFW
    tfw_path = output_path.with_suffix('.tfw')
    with open(tfw_path, 'w') as f:
        f.write(f"{x_pixel_size:.15f}\n")
        f.write("0.0\n")
        f.write("0.0\n")
        f.write(f"{y_pixel_size:.15f}\n")
        f.write(f"{x_origin:.15f}\n")
        f.write(f"{y_origin:.15f}\n")

    # 保存 PRJ
    prj_path = output_path.with_suffix('.prj')
    with open(prj_path, 'w') as f:
        f.write(WGS84_PRJ)

    return tif_path


def download_bbox(
    provider: TileProvider,
    bbox: tuple[float, float, float, float],
    output_path: Path,
    zoom: int = None,
    buffer_deg: float = 0.0005,
    max_tiles: int = 10000,
    progress_callback=None,
) -> dict:
    """
    下载指定 bbox 区域的影像

    Args:
        provider: TileProvider instance
        bbox: (lng_min, lat_min, lng_max, lat_max) in WGS-84
        output_path: 输出路径 (不含后缀)
        zoom: 指定 zoom level, None=自动选最高
        buffer_deg: bbox 扩大缓冲 (度)
        max_tiles: 最大瓦片数限制
        progress_callback: 进度回调 fn(downloaded, total)

    Returns:
        dict with metadata (zoom, year, width, height, file_size_mb, etc.)
    """
    lng_min, lat_min, lng_max, lat_max = bbox
    center_lat = (lat_min + lat_max) / 2
    center_lng = (lng_min + lng_max) / 2

    # Session
    session = requests.Session()
    session.headers.update(HEADERS)

    # 自动选择 zoom
    if zoom is None:
        zoom = find_best_zoom(provider, center_lat, center_lng, session)

    # 获取日期
    date_meta = get_capture_date(provider, center_lat, center_lng, zoom, session)

    resolution = meters_per_pixel(center_lat, zoom)

    # 扩大 bbox
    lng_min -= buffer_deg
    lat_min -= buffer_deg
    lng_max += buffer_deg
    lat_max += buffer_deg

    # 计算瓦片范围
    tile_info = bbox_to_tiles((lng_min, lat_min, lng_max, lat_max), zoom)

    if tile_info['total_tiles'] > max_tiles:
        raise ValueError(
            f"Too many tiles: {tile_info['total_tiles']} > {max_tiles}. "
            f"Reduce zoom or bbox size."
        )

    # 创建画布
    img_width = tile_info['n_tiles_x'] * TILE_SIZE
    img_height = tile_info['n_tiles_y'] * TILE_SIZE
    canvas = Image.new('RGB', (img_width, img_height), (255, 255, 255))

    # 下载瓦片
    downloaded = 0
    failed = 0
    total = tile_info['total_tiles']

    for ty in range(tile_info['tile_min_y'], tile_info['tile_max_y'] + 1):
        for tx in range(tile_info['tile_min_x'], tile_info['tile_max_x'] + 1):
            tile_img, _ = provider.download_tile(tx, ty, zoom, session)
            if tile_img:
                paste_x = (tx - tile_info['tile_min_x']) * TILE_SIZE
                paste_y = (ty - tile_info['tile_min_y']) * TILE_SIZE
                canvas.paste(tile_img, (paste_x, paste_y))
                downloaded += 1
            else:
                failed += 1

            if progress_callback:
                progress_callback(downloaded + failed, total)

            time.sleep(REQUEST_DELAY)

    # 裁剪到精确 bbox
    crop_left = tile_info['px_min_x'] - tile_info['tile_min_x'] * TILE_SIZE
    crop_top = tile_info['px_min_y'] - tile_info['tile_min_y'] * TILE_SIZE
    crop_right = tile_info['px_max_x'] - tile_info['tile_min_x'] * TILE_SIZE
    crop_bottom = tile_info['px_max_y'] - tile_info['tile_min_y'] * TILE_SIZE

    canvas = canvas.crop((crop_left, crop_top, crop_right, crop_bottom))

    # 精确地理范围
    actual_lat_max, actual_lng_min = pixel_to_lat_lng(
        tile_info['px_min_x'], tile_info['px_min_y'], zoom)
    actual_lat_min, actual_lng_max = pixel_to_lat_lng(
        tile_info['px_max_x'], tile_info['px_max_y'], zoom)

    # 保存
    tif_path = save_with_worldfile(
        canvas, output_path,
        actual_lng_min, actual_lat_min, actual_lng_max, actual_lat_max
    )

    width, height = canvas.size
    file_size_mb = tif_path.stat().st_size / (1024 * 1024)

    return {
        'provider': provider.name,
        'zoom': zoom,
        'resolution_m': round(resolution, 3),
        'width_px': width,
        'height_px': height,
        'file_size_mb': round(file_size_mb, 2),
        'tiles_ok': downloaded,
        'tiles_failed': failed,
        'bbox': [actual_lng_min, actual_lat_min, actual_lng_max, actual_lat_max],
        'filename': tif_path.name,
        **date_meta,
    }
