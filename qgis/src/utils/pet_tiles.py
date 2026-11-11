"""Join PET tile files into one map with a VRT.

Paths inside the VRT are stored relative to the VRT file, because the qgis
service mounts the data at /data and QGIS Server mounts it at /io/data.
"""
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from osgeo import gdal

TILE_OPTIONS = ['TILED=YES', 'COMPRESS=DEFLATE', 'PREDICTOR=3']
OVERVIEW_LEVELS = [2, 4, 8, 16]


def build_overviews(tile):
    """Internal overviews per tile: GDAL uses them when the VRT is read zoomed out."""
    ds = gdal.Open(str(tile), gdal.GA_Update)
    if ds.BuildOverviews('AVERAGE', OVERVIEW_LEVELS) != 0:
        raise RuntimeError(f'Could not build overviews for {tile}')
    ds = None


def build_vrt(vrt_path, sources, nodata=-9999):
    """One VRT over all tile files, with relative paths."""
    vrt_path = Path(vrt_path)
    partial = vrt_path.with_name(vrt_path.stem + '.partial.vrt')
    ds = gdal.BuildVRT(str(partial), [str(p) for p in sources], srcNodata=nodata, VRTNodata=nodata)
    if ds is None:
        raise RuntimeError(f'Could not build {vrt_path}')
    ds = None
    base = partial.resolve().parent
    tree = ET.parse(partial)
    for element in tree.iter('SourceFilename'):
        path = Path(element.text)
        if element.get('relativeToVRT') == '1':
            path = base/path
        element.text = os.path.relpath(path.resolve(), base).replace(os.sep, '/')
        element.set('relativeToVRT', '1')
    tree.write(partial)
    partial.replace(vrt_path)
    return vrt_path
