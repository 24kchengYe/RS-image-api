"""
Imagery providers: Google, Bing, Esri, Tianditu
Each provider implements tile URL generation and metadata extraction.
"""

import time
import requests
from io import BytesIO
from PIL import Image

from .tile_system import tile_to_quadkey, TILE_SIZE

# ============ Base Config ============

RETRY = 3
EMPTY_TILE_THRESHOLD = 1500  # bytes below this = empty tile
REQUEST_DELAY = 0.02  # seconds between requests (politeness)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
}


# ============ Provider Definitions ============

class TileProvider:
    """Base class for tile providers"""

    name: str = "base"
    max_zoom: int = 19
    min_zoom: int = 1
    crs: str = "EPSG:4326"  # Output CRS (all providers output WGS-84)

    def get_tile_url(self, x: int, y: int, z: int) -> str:
        raise NotImplementedError

    def get_metadata(self, response: requests.Response) -> dict:
        """Extract metadata (date, etc.) from response headers"""
        return {}

    def is_valid_tile(self, response: requests.Response) -> bool:
        """Check if response contains valid imagery (not blank)"""
        return (
            response.status_code == 200
            and len(response.content) > EMPTY_TILE_THRESHOLD
        )

    def download_tile(self, x: int, y: int, z: int, session: requests.Session) -> tuple:
        """
        Download a single tile.
        Returns: (PIL.Image or None, metadata_dict)
        """
        url = self.get_tile_url(x, y, z)

        for attempt in range(RETRY):
            try:
                resp = session.get(url, timeout=15)
                metadata = self.get_metadata(resp)

                if self.is_valid_tile(resp):
                    img = Image.open(BytesIO(resp.content)).convert('RGB')
                    return img, metadata
                else:
                    return None, metadata

            except Exception:
                if attempt < RETRY - 1:
                    time.sleep(1)

        return None, {}


class GoogleProvider(TileProvider):
    """
    Google Maps Satellite Tiles
    - z20 is the real max; z21+ are interpolated upscales (not extra detail)
    - Outside China: WGS-84 (no offset)
    - China mainland: GCJ-02 (5-10m offset)
    - No capture date in response headers
    - For dated imagery, use GEHistoricalImagery backend instead
    """

    name = "google"
    max_zoom = 20  # z21 exists but is just interpolated upscale
    min_zoom = 1

    def get_tile_url(self, x: int, y: int, z: int) -> str:
        # mt0-mt3 负载均衡
        server = (x + y) % 4
        return f"https://mt{server}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"

    def get_metadata(self, response: requests.Response) -> dict:
        return {
            'date': response.headers.get('Date', ''),
            'cache_control': response.headers.get('Cache-Control', ''),
        }


class BingProvider(TileProvider):
    """
    Bing Maps Aerial Tiles (Microsoft)
    - 全球 WGS-84 (无偏移)
    - 响应头有 CaptureDateMaxYYMM 日期元数据
    - 与 Overture Maps 建筑轮廓坐标完全一致
    """

    name = "bing"
    max_zoom = 20
    min_zoom = 1

    def get_tile_url(self, x: int, y: int, z: int) -> str:
        quadkey = tile_to_quadkey(x, y, z)
        subdomain = (x + y) % 4
        return f"https://ecn.t{subdomain}.tiles.virtualearth.net/tiles/a{quadkey}.jpeg?g=14628"

    def get_metadata(self, response: requests.Response) -> dict:
        date_max = response.headers.get('X-VE-TILEMETA-CaptureDateMaxYYMM', '')
        date_range = response.headers.get('X-VE-TILEMETA-CaptureDatesRange', '')

        year = ''
        if len(date_max) >= 2:
            year = '20' + date_max[:2]

        return {
            'capture_year': year,
            'capture_yymm': date_max,
            'capture_range': date_range,
        }


class EsriProvider(TileProvider):
    """
    Esri World Imagery
    - 全球 WGS-84
    - 商业级影像 (Maxar, DigitalGlobe)
    - 质量通常很高
    """

    name = "esri"
    max_zoom = 20
    min_zoom = 1

    def get_tile_url(self, x: int, y: int, z: int) -> str:
        return f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"

    def get_metadata(self, response: requests.Response) -> dict:
        return {
            'date': response.headers.get('Date', ''),
        }


class TiandituProvider(TileProvider):
    """
    天地图 (Tianditu) 卫星影像
    - 中国区域: CGCS2000 (≈ WGS-84, 差异亚米级)
    - 需要 API token
    - 中国境内分辨率最高
    """

    name = "tianditu"
    max_zoom = 18
    min_zoom = 1

    def __init__(self, token: str = ""):
        self.token = token

    def get_tile_url(self, x: int, y: int, z: int) -> str:
        server = (x + y) % 8
        return f"https://t{server}.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILECOL={x}&TILEROW={y}&TILEMATRIX={z}&tk={self.token}"


# ============ Provider Registry ============

PROVIDERS = {
    'google': GoogleProvider,
    'bing': BingProvider,
    'esri': EsriProvider,
    'tianditu': TiandituProvider,
}


def get_provider(name: str, **kwargs) -> TileProvider:
    """Get provider instance by name"""
    if name not in PROVIDERS:
        raise ValueError(f"Unknown provider: {name}. Available: {list(PROVIDERS.keys())}")
    return PROVIDERS[name](**kwargs)
