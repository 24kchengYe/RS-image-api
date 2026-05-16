"""
RS-image-api: Multi-source Remote Sensing Imagery Downloader
支持 Google Maps / Bing Maps / Esri 等多源卫星影像批量下载

Features:
- 多源支持: Google, Bing, Esri, 天地图
- 自动选择最高 zoom
- 影像日期检测
- 输入: bbox / shapefile / gpkg
- 输出: GeoTIFF (WGS-84)
- 支持批量下载 + 断点续传
"""

__version__ = "0.1.0"
