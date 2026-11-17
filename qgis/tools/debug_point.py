#!/usr/bin/env python3
"""Compare one point between the Middelburg (main) PET and the tiles PET, step by step.

Recomputes the tile that contains the point exactly like zeeland_tiles.py does,
keeps the intermediate values, and prints them next to the values in the
Middelburg intermediate files (/data/uhi, /data/raster, /data/shadow-maps).

    python3 /app/tools/debug_point.py 31950 391650
"""
import math
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zeeland_tiles as zt  # noqa: E402

RES = 1.0
MIDDELBURG = {
    'pet (final)':        '/data/pet/pet.tif',
    'sun pet':            '/data/uhi/sun-pet.tif',
    'shadow pet':         '/data/uhi/shadow-pet.tif',
    'sun partial (cell)': '/data/uhi/sun-bbox.tif',
    'shadow partial':     '/data/uhi/shadow-bbox.tif',
    't_a (cell)':         '/data/uhi/t_a.tif',
    'svf (filled)':       '/data/raster/svf-reproject-filled.tif',
    'bowen':              '/data/raster/br-reproject.tif',
    'vegetation':         '/data/raster/vegetation-reproject.tif',
    'hillshade':          '/data/shadow-maps/hillshade_20150701_1500.tif',
}
ZONES = '/data/json/wind_reduction copy.geojson'
ZONE_FIELDS = ['svf_mean', 'veg_mean', 'uhi', 't_a', 't_w', 'geschaalde_u_1_2',
               'pet_sun_partial', 'pet_shadow_partial']


def sample(path, x, y):
    """Value of the pixel at (x, y), or a short reason why there is none."""
    if not Path(path).exists():
        return 'file missing'
    ds = gdal.Open(path)
    gt = ds.GetGeoTransform()
    col, row = int((x - gt[0]) / gt[1]), int((y - gt[3]) / gt[5])
    if not (0 <= col < ds.RasterXSize and 0 <= row < ds.RasterYSize):
        return 'outside file'
    band = ds.GetRasterBand(1)
    value = float(band.ReadAsArray(col, row, 1, 1)[0, 0])
    nodata = band.GetNoDataValue()
    return 'NoData' if (nodata is not None and value == nodata) or not math.isfinite(value) else value


def middelburg_zone(x, y):
    if not Path(ZONES).exists():
        return {}
    ds = ogr.Open(ZONES)
    layer = ds.GetLayer()
    point = ogr.CreateGeometryFromWkt(f'POINT({x} {y})')
    names = [layer.GetLayerDefn().GetFieldDefn(i).GetName() for i in range(layer.GetLayerDefn().GetFieldCount())]
    for feature in layer:
        if feature.GetGeometryRef().Contains(point):
            return {f: feature.GetField(f) for f in ZONE_FIELDS if f in names}
    return {}


def tiles_values(x, y):
    """Recompute the tile around (x, y) like calculate_tile() and return the values at the point."""
    tile_m = 2800
    col, row = math.floor((x - zt.ORIGIN_X) / tile_m), math.floor((zt.ORIGIN_Y - y) / tile_m)
    tile = zt.Box(zt.ORIGIN_X + col*tile_m, zt.ORIGIN_Y - (row+1)*tile_m,
                  zt.ORIGIN_X + (col+1)*tile_m, zt.ORIGIN_Y - row*tile_m)
    work = tile.expand(zt.CELL_W, zt.CELL_H)
    out = Path('/tmp/debug_point'); out.mkdir(exist_ok=True)

    classes = zt.warp(f'/vsizip/{zt.SOURCE}/sources/lgn2018/LGN2018.zip/LGN2018/LGN2018.tif', work, RES, 'near', gdal.GDT_Int16)
    land = np.isfinite(classes) & (classes > 0)
    paved = np.isin(classes, zt.PAVED)
    bowen = np.where(land, np.where(paved, 3.0, 0.4), np.nan)
    vegetation = np.where(land, np.where(paved, 0.0, 1.0), np.nan)
    svf_raw = zt.warp(zt.svf_vrt(out), work, RES, 'average')
    svf = zt.fill_nodata(svf_raw, work, RES, distance_px=10)
    dsm = zt.warp(zt.SOURCE/'acquisition-20261005/elevation-dsm/native.vrt', work, RES/2, 'average')

    sun_partial, shadow_partial, t_a = zt.zonal_partial_pet(vegetation, svf_raw, RES)
    sun_pet = sun_partial + 0.546*bowen + 1.94*svf
    shadow_pet = shadow_partial + 0.015*svf*zt.Q_DIFF + 0.0060*(1-svf)*zt.BOLTZMANN*((t_a+273.15)**4)
    shade = zt.hillshade(dsm, work, RES/2, RES)
    pet = np.where(shade > zt.SHADOW_THRESHOLD, sun_pet, shadow_pet)

    px, py = int((x - work.left) / RES), int((work.top - y) / RES)
    cx, cy = int((x - work.left) / zt.CELL_W), int((work.top - y) / zt.CELL_H)
    return {
        'cell svf_mean': float(zt.cell_means(svf_raw, RES)[cy, cx]),
        'cell veg_mean': float(zt.cell_means(vegetation, RES)[cy, cx]),
        't_a (cell)': float(t_a[py, px]),
        'wind u (placeholder)': zt.WIND_PLACEHOLDER,
        'sun partial (cell)': float(sun_partial[py, px]),
        'shadow partial': float(shadow_partial[py, px]),
        'svf (filled)': float(svf[py, px]),
        'bowen': float(bowen[py, px]),
        'vegetation': float(vegetation[py, px]),
        'hillshade': float(shade[py, px]),
        'sun pet': float(sun_pet[py, px]),
        'shadow pet': float(shadow_pet[py, px]),
        'pet (final)': float(pet[py, px]),
        'LGN class': int(classes[py, px]) if np.isfinite(classes[py, px]) else 'NoData',
    }


def show(value):
    return f'{value:10.3f}' if isinstance(value, float) else f'{str(value):>10}'


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    x, y = float(sys.argv[1]), float(sys.argv[2])
    gdal.UseExceptions()
    print(f'Point {x}, {y}\n')

    zone = middelburg_zone(x, y)
    print('Middelburg zone cell (from the wind GeoJSON):')
    for k, v in zone.items() or [('(not found)', '')]:
        print(f'  {k:22}{show(v)}')

    tiles = tiles_values(x, y)
    print(f'\n{"":24}{"Middelburg":>12}{"Tiles":>12}')
    for key in MIDDELBURG:
        print(f'  {key:22}{show(sample(MIDDELBURG[key], x, y)):>12}{show(tiles.get(key, "-")):>12}')
    for key in ('cell svf_mean', 'cell veg_mean', 'wind u (placeholder)', 'LGN class'):
        print(f'  {key:22}{"":>12}{show(tiles[key]):>12}')


if __name__ == '__main__':
    main()
    