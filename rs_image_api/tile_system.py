"""
Web Mercator / Slippy Map Tile System utilities
Shared by all providers (Google, Bing, Esri, etc.)
"""

import math

TILE_SIZE = 256


def lat_lng_to_pixel(lat: float, lng: float, zoom: int) -> tuple[int, int]:
    """WGS-84 经纬度 → 像素坐标"""
    sin_lat = math.sin(lat * math.pi / 180)
    sin_lat = max(-0.9999, min(0.9999, sin_lat))
    map_size = TILE_SIZE * (2 ** zoom)
    pixel_x = ((lng + 180) / 360) * map_size
    pixel_y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * map_size
    return int(pixel_x), int(pixel_y)


def pixel_to_lat_lng(pixel_x: int, pixel_y: int, zoom: int) -> tuple[float, float]:
    """像素坐标 → WGS-84 经纬度"""
    map_size = TILE_SIZE * (2 ** zoom)
    lng = (pixel_x / map_size) * 360 - 180
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * pixel_y / map_size)))
    lat = lat_rad * 180 / math.pi
    return lat, lng


def pixel_to_tile(pixel_x: int, pixel_y: int) -> tuple[int, int]:
    """像素坐标 → 瓦片坐标"""
    return pixel_x // TILE_SIZE, pixel_y // TILE_SIZE


def tile_to_quadkey(tile_x: int, tile_y: int, zoom: int) -> str:
    """瓦片坐标 → Bing quadkey"""
    quadkey = []
    for i in range(zoom, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if (tile_x & mask) != 0:
            digit += 1
        if (tile_y & mask) != 0:
            digit += 2
        quadkey.append(str(digit))
    return ''.join(quadkey)


def bbox_to_tiles(bbox: tuple[float, float, float, float], zoom: int) -> dict:
    """
    bbox (lng_min, lat_min, lng_max, lat_max) → tile range info

    Returns dict with:
        tile_min_x, tile_min_y, tile_max_x, tile_max_y,
        px_min_x, px_min_y, px_max_x, px_max_y,
        n_tiles_x, n_tiles_y, total_tiles
    """
    lng_min, lat_min, lng_max, lat_max = bbox

    px_min_x, px_min_y = lat_lng_to_pixel(lat_max, lng_min, zoom)
    px_max_x, px_max_y = lat_lng_to_pixel(lat_min, lng_max, zoom)

    tile_min_x, tile_min_y = pixel_to_tile(px_min_x, px_min_y)
    tile_max_x, tile_max_y = pixel_to_tile(px_max_x, px_max_y)

    n_tiles_x = tile_max_x - tile_min_x + 1
    n_tiles_y = tile_max_y - tile_min_y + 1

    return {
        'tile_min_x': tile_min_x, 'tile_min_y': tile_min_y,
        'tile_max_x': tile_max_x, 'tile_max_y': tile_max_y,
        'px_min_x': px_min_x, 'px_min_y': px_min_y,
        'px_max_x': px_max_x, 'px_max_y': px_max_y,
        'n_tiles_x': n_tiles_x, 'n_tiles_y': n_tiles_y,
        'total_tiles': n_tiles_x * n_tiles_y,
    }


def meters_per_pixel(lat: float, zoom: int) -> float:
    """计算给定纬度和zoom下的地面分辨率 (m/pixel)"""
    return 156543.03392 * math.cos(lat * math.pi / 180) / (2 ** zoom)
