"""
RS-image-api: Multi-source Remote Sensing Imagery Downloader

Supports Google Earth Historical (via GEHistoricalImagery), Bing Maps,
Google Maps, Esri World Imagery, and Tianditu.
"""

__version__ = "0.2.0"

from .config import setup as _setup
_setup()
