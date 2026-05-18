"""
VRT generation and merge utilities.
Creates virtual rasters from directories of GeoTIFFs.
"""

import struct
from pathlib import Path
from typing import Optional


def read_geotiff_geo(tif_path: Path) -> Optional[dict]:
    """
    Read GeoTIFF geographic info directly from TIFF tags.
    No external dependencies needed.

    Returns:
        {'width', 'height', 'scale_x', 'scale_y', 'origin_x', 'origin_y',
         'lng_min', 'lat_min', 'lng_max', 'lat_max'}
    """
    try:
        with open(tif_path, "rb") as f:
            header = f.read(8)
            if len(header) < 8:
                return None

            byte_order = header[:2]
            endian = "<" if byte_order == b"II" else ">"
            magic = struct.unpack(endian + "H", header[2:4])[0]

            if magic == 43:  # BigTIFF
                return None
            if magic != 42:
                return None

            ifd_offset = struct.unpack(endian + "I", header[4:8])[0]
            f.seek(ifd_offset)

            data = f.read(2)
            if len(data) < 2:
                return None
            num_entries = struct.unpack(endian + "H", data)[0]

            tags = {}
            for _ in range(num_entries):
                entry = f.read(12)
                if len(entry) < 12:
                    break

                tag_id = struct.unpack(endian + "H", entry[0:2])[0]
                tag_type = struct.unpack(endian + "H", entry[2:4])[0]

                # Width=256, Height=257
                if tag_id in [256, 257]:
                    if tag_type == 3:  # SHORT
                        tags[tag_id] = struct.unpack(endian + "H", entry[8:10])[0]
                    elif tag_type == 4:  # LONG
                        tags[tag_id] = struct.unpack(endian + "I", entry[8:12])[0]

                # ModelPixelScaleTag=33550, ModelTiepointTag=33922
                if tag_id in [33550, 33922]:
                    offset = struct.unpack(endian + "I", entry[8:12])[0]
                    pos = f.tell()
                    f.seek(offset)
                    if tag_id == 33550:
                        sx = struct.unpack(endian + "d", f.read(8))[0]
                        sy = struct.unpack(endian + "d", f.read(8))[0]
                        tags["scale"] = (sx, sy)
                    elif tag_id == 33922:
                        vals = struct.unpack(endian + "6d", f.read(48))
                        tags["tiepoint"] = vals
                    f.seek(pos)

            w = tags.get(256, 0)
            h = tags.get(257, 0)

            if "scale" in tags and "tiepoint" in tags and w > 0:
                sx, sy = tags["scale"]
                _, _, _, ox, oy, _ = tags["tiepoint"]
                return {
                    "width": w, "height": h,
                    "scale_x": sx, "scale_y": sy,
                    "origin_x": ox, "origin_y": oy,
                    "lng_min": ox, "lat_max": oy,
                    "lng_max": ox + sx * w,
                    "lat_min": oy - sy * h,
                }
    except Exception:
        pass

    return None


def read_worldfile_geo(tif_path: Path) -> Optional[dict]:
    """
    Read geographic info from .tfw world file + image dimensions.
    Fallback when GeoTIFF tags are not available.
    """
    tfw_path = tif_path.with_suffix(".tfw")
    if not tfw_path.exists():
        return None

    try:
        from PIL import Image
        img = Image.open(tif_path)
        w, h = img.size
        img.close()
    except Exception:
        return None

    with open(tfw_path) as f:
        lines = f.readlines()

    x_ps = float(lines[0])
    y_ps = float(lines[3])  # negative
    x_orig = float(lines[4])
    y_orig = float(lines[5])

    lng_min = x_orig - x_ps / 2
    lat_max = y_orig - y_ps / 2
    lng_max = lng_min + x_ps * w
    lat_min = lat_max + y_ps * h

    return {
        "width": w, "height": h,
        "scale_x": x_ps, "scale_y": abs(y_ps),
        "origin_x": lng_min, "origin_y": lat_max,
        "lng_min": lng_min, "lat_min": lat_min,
        "lng_max": lng_max, "lat_max": lat_max,
    }


def collect_tif_info(input_dir: Path, pattern: str = "*.tif") -> list[dict]:
    """Collect geographic info from all TIFFs in a directory."""
    tifs = sorted(input_dir.glob(pattern))
    images = []

    for t in tifs:
        # Try GeoTIFF tags first, then world file
        info = read_geotiff_geo(t)
        if not info:
            info = read_worldfile_geo(t)
        if info:
            info["path"] = t.name
            images.append(info)

    return images


def generate_vrt(input_dir: Path, output_path: Path = None,
                 pattern: str = "*.tif") -> Path:
    """
    Generate a VRT (Virtual Raster) file combining all TIFFs in a directory.

    Args:
        input_dir: directory containing GeoTIFFs
        output_path: output .vrt file path (default: input_dir/merged.vrt)
        pattern: glob pattern for TIFFs

    Returns:
        Path to generated VRT file
    """
    if output_path is None:
        output_path = input_dir / "merged.vrt"

    images = collect_tif_info(input_dir, pattern)
    if not images:
        raise ValueError(f"No georeferenced TIFFs found in {input_dir}")

    # Global bounds
    g_lng_min = min(i["lng_min"] for i in images)
    g_lat_min = min(i["lat_min"] for i in images)
    g_lng_max = max(i["lng_max"] for i in images)
    g_lat_max = max(i["lat_max"] for i in images)

    # Use smallest pixel size (highest resolution)
    x_res = min(i["scale_x"] for i in images)
    y_res = min(i["scale_y"] for i in images)

    total_width = int(round((g_lng_max - g_lng_min) / x_res))
    total_height = int(round((g_lat_max - g_lat_min) / y_res))

    # Build VRT XML
    lines = [
        f'<VRTDataset rasterXSize="{total_width}" rasterYSize="{total_height}">',
        '  <SRS>EPSG:4326</SRS>',
        f'  <GeoTransform>{g_lng_min}, {x_res}, 0, {g_lat_max}, 0, -{y_res}</GeoTransform>',
    ]

    for band_idx in range(1, 4):
        color = ["Red", "Green", "Blue"][band_idx - 1]
        lines.append(f'  <VRTRasterBand dataType="Byte" band="{band_idx}">')
        lines.append(f'    <ColorInterp>{color}</ColorInterp>')

        for img in images:
            x_off = int(round((img["lng_min"] - g_lng_min) / x_res))
            y_off = int(round((g_lat_max - img["lat_max"]) / y_res))
            dst_w = int(round((img["lng_max"] - img["lng_min"]) / x_res))
            dst_h = int(round((img["lat_max"] - img["lat_min"]) / y_res))

            lines.extend([
                "    <SimpleSource>",
                f'      <SourceFilename relativeToVRT="1">{img["path"]}</SourceFilename>',
                f"      <SourceBand>{band_idx}</SourceBand>",
                f'      <SourceProperties RasterXSize="{img["width"]}" RasterYSize="{img["height"]}" DataType="Byte" />',
                f'      <SrcRect xOff="0" yOff="0" xSize="{img["width"]}" ySize="{img["height"]}" />',
                f'      <DstRect xOff="{x_off}" yOff="{y_off}" xSize="{dst_w}" ySize="{dst_h}" />',
                "    </SimpleSource>",
            ])

        lines.append("  </VRTRasterBand>")

    lines.append("</VRTDataset>")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return output_path
