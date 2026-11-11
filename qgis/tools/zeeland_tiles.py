#!/usr/bin/env python3
"""Zeeland PET map, generated tile by tile (tiles approach for the proof of concept).

Every tile is calculated on its own, with a one-grid-cell margin (halo) so the
edges match their neighbours. Intermediate rasters stay in memory and only the
tile's final PET is written, so memory and working disk depend on the tile
size, not on the size of Zeeland. A VRT joins the tiles into one map.

The PET formulas are the ones in src/services/pet_service.py (main). Because the
zones are grid cells that line up with the pixels, the zonal means and the
rasterisation are done with numpy instead of QGIS vector round trips.

    python3 tools/zeeland_tiles.py --resolution 2 --out /out/pilot-2m-tiled --bbox 30000,388150,35600,393750
    python3 tools/zeeland_tiles.py --resolution 1 --out /out/zeeland-1m        # all of Zeeland
"""
import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import resource
import sys
import time
import warnings

import numpy as np
from osgeo import gdal, ogr, osr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.utils.pet_tiles import TILE_OPTIONS, build_overviews, build_vrt
from src.utils.solar_position import get_solar_position
from src.utils.uhi_lookup_tables import UHILookupTables

SOURCE = Path(os.environ.get('ZEELAND_SOURCE', '/data/zeeland'))
NODATA = -9999.0

# Zone grid: 140 x 280 m cells on a fixed lattice (same cell size and lattice as the 2 m run).
CELL_W, CELL_H = 140, 280
ORIGIN_X, ORIGIN_Y = 2000, 427350   # NW of Zeeland, so tile numbers are positive
WIND_PLACEHOLDER = 7.5          # geschaalde_u_1_2 (m/s): placeholder until the wind team delivers
PAVED = [18, 19, 24, 25, 26, 27]  # LGN2018 classes treated as paved
WATER = [16, 17]                  # LGN2018 water classes, masked out

# Defaults from main's /pet/full-map-generation and pet_service.py
T_MIN, T_MAX, AVERAGE_WIND = 27.2, 29.1, 7.5
BASE_TEMPERATURE, UHI_DATETIME = 28.3, datetime(2017, 7, 1, 18, 0)
RELATIVE_HUMIDITY, PHI, Q_GL, Q_DIFF = 44.0, 44.0, 663.0, 0.2
SHADOW_DATETIME, LAT, LON = datetime(2015, 7, 1, 15, 0), 51.498, 3.613
SHADOW_THRESHOLD = 127
BOLTZMANN = 5.670374419e-8


@dataclass(frozen=True)
class Box:
    left: float
    bottom: float
    right: float
    top: float

    def expand(self, dx, dy):
        return Box(self.left-dx, self.bottom-dy, self.right+dx, self.top+dy)

    def wkt(self):
        l, b, r, t = self.left, self.bottom, self.right, self.top
        return f'POLYGON(({l} {b},{r} {b},{r} {t},{l} {t},{l} {b}))'


# ---------- inputs ----------

def svf_vrt(out_dir):
    """One VRT over the SVF source tiles listed in sources/svf-nl-3/manifest.json."""
    target = out_dir/'svf-sources.vrt'
    if target.exists():
        return target
    manifest = json.loads((SOURCE/'sources/svf-nl-3/manifest.json').read_text())
    paths = []
    for entry in manifest['files']:
        recorded = entry['local_path'].replace('\\', '/')
        for prefix in ('/data/zeeland/', 'data/zeeland/'):
            if recorded.startswith(prefix):
                recorded = str(SOURCE/recorded[len(prefix):])
                break
        if not Path(recorded).is_file():
            raise FileNotFoundError(f'SVF source from the manifest is missing: {recorded}')
        paths.append(recorded)
    ds = gdal.BuildVRT(str(target), paths)
    if ds is None:
        raise RuntimeError('Could not build the SVF mosaic')
    ds = None
    return target


def warp(source, bounds, resolution, algorithm, dtype=gdal.GDT_Float32, nodata=NODATA):
    """Read `source` on exactly this grid, as a numpy array with NaN for NoData."""
    ds = gdal.Warp('', str(source), format='MEM', outputBounds=(bounds.left, bounds.bottom, bounds.right, bounds.top),
                   xRes=resolution, yRes=resolution, dstSRS='EPSG:28992', resampleAlg=algorithm,
                   dstNodata=nodata, outputType=dtype, warpMemoryLimit=256*1024**2)
    if ds is None:
        raise RuntimeError(f'Could not read {source}')
    values = ds.GetRasterBand(1).ReadAsArray().astype('float32')
    values[values == nodata] = np.nan
    return values


def to_gdal(values, bounds, resolution, dtype=gdal.GDT_Float32, nodata=NODATA, path=None, options=None):
    """numpy array (NaN = NoData) -> GDAL dataset on the given grid (MEM unless a path is given)."""
    driver = gdal.GetDriverByName('GTiff' if path else 'MEM')
    rows, cols = values.shape
    ds = driver.Create(str(path) if path else '', cols, rows, 1, dtype, options or [])
    ds.SetGeoTransform((bounds.left, resolution, 0, bounds.top, 0, -resolution))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(28992)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(np.where(np.isnan(values), nodata, values) if values.dtype.kind == 'f' else values)
    return ds


def boundary_mask(boundary_layer, bounds, resolution):
    rows = round((bounds.top-bounds.bottom)/resolution)
    cols = round((bounds.right-bounds.left)/resolution)
    ds = to_gdal(np.zeros((rows, cols), dtype='uint8'), bounds, resolution, gdal.GDT_Byte, nodata=255)
    gdal.RasterizeLayer(ds, [1], boundary_layer, burn_values=[1])
    return ds.GetRasterBand(1).ReadAsArray() == 1


# ---------- per-cell (zonal) part: formulas from pet_service.py ----------

def cell_means(values, resolution):
    """Mean of each 140 x 280 m cell, ignoring NoData (what QgsZonalStatistics computes for these cells)."""
    cw, ch = round(CELL_W/resolution), round(CELL_H/resolution)
    rows, cols = values.shape
    blocks = values.reshape(rows//ch, ch, cols//cw, cw)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)  # all-NoData cells give NaN, as None in QGIS
        return np.nanmean(blocks, axis=(1, 3))


def to_pixels(cells, resolution):
    cw, ch = round(CELL_W/resolution), round(CELL_H/resolution)
    return np.repeat(np.repeat(cells, ch, axis=0), cw, axis=1)


def zonal_partial_pet(vegetation, svf_raw, resolution):
    """calculate_zonal_uhi -> calculate_t_a_temperature -> calculate_wet_bulb_temp -> partial PET sun/shadow."""
    svf_mean, veg_mean = cell_means(svf_raw, resolution), cell_means(vegetation, resolution)
    base = ((663 * (T_MAX-T_MIN)**3) / AVERAGE_WIND) ** 0.25
    uhi = (2 - svf_mean - veg_mean) * base
    t_a = BASE_TEMPERATURE + uhi * UHILookupTables.get_uhi_factor(UHI_DATETIME)
    r_h = RELATIVE_HUMIDITY
    t_w = (t_a * math.atan(0.151977 * math.sqrt(r_h + 8.313659)) + np.arctan(t_a + r_h)
           - math.atan(r_h - 1.676331) + 0.00391838 * r_h**1.5 * math.atan(0.023101 * r_h) - 4.686035)
    u = WIND_PLACEHOLDER
    sun = (-13.26 + 1.25*t_a + 0.011*Q_GL - 3.37*math.log(u) + 0.078*t_w
           + 0.0055*Q_GL*math.log(u) + 5.56*math.sin(PHI) - 0.0103*Q_GL*math.log(u)*math.sin(PHI))
    shadow = -12.14 + 1.25*t_a - 1.47*math.log(u) + 0.060*t_w
    return to_pixels(sun, resolution), to_pixels(shadow, resolution), to_pixels(t_a, resolution)


# ---------- one tile ----------

def fill_nodata(values, bounds, resolution, distance_px):
    """gdal:fillnodata (as raster_service.fill_nodata_gdal), on an in-memory copy."""
    ds = to_gdal(values, bounds, resolution)
    band = ds.GetRasterBand(1)
    gdal.FillNodata(band, None, distance_px, 0, options=['TEMP_FILE_DRIVER=MEM'])  # work files in memory, not in the (read-only) current folder
    filled = band.ReadAsArray().astype('float32')
    filled[filled == NODATA] = np.nan
    return filled


def hillshade(dsm, bounds, dsm_resolution, pet_resolution):
    """gdal:hillshade as in shadow_service.py, then aligned to the PET grid with nearest neighbour."""
    azimuth, altitude = get_solar_position(LAT, LON, 'Middelburg', 'Netherlands', 'Europe/Amsterdam', SHADOW_DATETIME)
    shade = gdal.DEMProcessing('', to_gdal(dsm, bounds, dsm_resolution), 'hillshade', format='MEM',
                               zFactor=1.0, azimuth=azimuth, altitude=altitude, computeEdges=False)
    aligned = gdal.Warp('', shade, format='MEM', xRes=pet_resolution, yRes=pet_resolution,
                        outputBounds=(bounds.left, bounds.bottom, bounds.right, bounds.top), resampleAlg='near')
    band = aligned.GetRasterBand(1)
    values = band.ReadAsArray().astype('float32')
    nodata = band.GetNoDataValue()
    if nodata is not None:
        values[values == nodata] = np.nan
    return values


def calculate_tile(tile, resolution, boundary_layer, svf_source, path):
    """Calculate one tile (plus halo) and write only the tile itself to `path`."""
    work = tile.expand(CELL_W, CELL_H)                    # one grid cell of context on every side
    dsm_resolution = resolution / 2                       # elevation at twice the PET detail, as main and the 2 m run do
    classes = warp(f'/vsizip/{SOURCE}/sources/lgn2018/LGN2018.zip/LGN2018/LGN2018.tif', work, resolution, 'near', gdal.GDT_Int16)
    land = np.isfinite(classes) & (classes > 0)
    paved = np.isin(classes, PAVED)
    bowen = np.where(land, np.where(paved, 3.0, 0.4), np.nan).astype('float32')        # same rule as the 2 m run
    vegetation = np.where(land, np.where(paved, 0.0, 1.0), np.nan).astype('float32')
    svf_raw = warp(svf_source, work, resolution, 'average')
    svf = fill_nodata(svf_raw, work, resolution, distance_px=max(1, round(10/resolution)))  # 10 m search
    dsm = warp(SOURCE/'acquisition-20261005/elevation-dsm/native.vrt', work, dsm_resolution, 'average')

    sun_partial, shadow_partial, t_a = zonal_partial_pet(vegetation, svf_raw, resolution)
    sun_pet = sun_partial + 0.546*bowen + 1.94*svf                                         # calculate_total_pet_sun
    shadow_pet = (shadow_partial + 0.015*svf*Q_DIFF
                  + 0.0060*(1-svf)*BOLTZMANN*((t_a+273.15)**4))                            # calculate_total_pet_shadow
    shade = hillshade(dsm, work, dsm_resolution, resolution)
    pet = np.where(shade > SHADOW_THRESHOLD, sun_pet, shadow_pet)                          # calculate_total_pet_map
    pet[np.isnan(shade)] = np.nan

    # Keep only the tile (drop the halo) and mask what is not supported land in Zeeland.
    hx, hy = round(CELL_W/resolution), round(CELL_H/resolution)
    core = (slice(hy, pet.shape[0]-hy), slice(hx, pet.shape[1]-hx))
    keep = (boundary_mask(boundary_layer, tile, resolution) & land[core] & ~np.isin(classes[core], WATER)
            & np.isfinite(pet[core]) & (svf[core] >= 0) & (svf[core] <= 1))
    result = np.where(keep, pet[core], np.nan).astype('float32')
    partial = path.with_name(path.stem + '.partial.tif')
    ds = to_gdal(result, tile, resolution, path=partial, options=TILE_OPTIONS)
    ds = None
    build_overviews(partial)
    partial.replace(path)
    return int(keep.sum())


# ---------- tile list, run, report ----------

def tile_list(boundary_layer, tile_m, bbox=None):
    left, right, bottom, top = boundary_layer.GetExtent()
    if bbox:
        left, bottom, right, top = bbox
    col0 = math.floor((left-ORIGIN_X)/tile_m)
    col1 = math.ceil((right-ORIGIN_X)/tile_m)
    row0 = math.floor((ORIGIN_Y-top)/tile_m)
    row1 = math.ceil((ORIGIN_Y-bottom)/tile_m)
    geometries = [f.GetGeometryRef().Clone() for f in boundary_layer]
    boundary_layer.ResetReading()
    tiles = []
    for row in range(row0, row1):
        for col in range(col0, col1):
            box = Box(ORIGIN_X+col*tile_m, ORIGIN_Y-(row+1)*tile_m, ORIGIN_X+(col+1)*tile_m, ORIGIN_Y-row*tile_m)
            shape = ogr.CreateGeometryFromWkt(box.wkt())
            if any(g.Intersects(shape) for g in geometries):   # skip tiles that are only sea
                tiles.append((col, row, box))
    return tiles


def peak_memory_mib():
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return round(max(own, children)/1024)


def save(path, value):
    partial = path.with_suffix('.partial.json')
    partial.write_text(json.dumps(value, indent=2)+'\n')
    partial.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--resolution', type=float, required=True, choices=[1, 2], help='PET pixel size in metres')
    parser.add_argument('--out', type=Path, required=True, help='output folder, e.g. /out/zeeland-2m')
    parser.add_argument('--tile-m', type=int, default=2800, help='tile size in metres (multiple of 280)')
    parser.add_argument('--bbox', help='only tiles touching left,bottom,right,top (RD New metres)')
    parser.add_argument('--max-tiles', type=int, default=0, help='stop after this many new tiles (timing sample)')
    args = parser.parse_args()
    if args.tile_m % CELL_H or args.tile_m % CELL_W:
        raise SystemExit('--tile-m must be a multiple of 140 and 280, e.g. 2800')
    gdal.UseExceptions()
    gdal.SetCacheMax(256*1024**2)

    out, tiles_dir = args.out, args.out/'tiles'
    tiles_dir.mkdir(parents=True, exist_ok=True)
    boundary = ogr.Open(str(SOURCE/'boundary/zeeland-rd.gpkg'))
    layer = boundary.GetLayer()
    bbox = [float(v) for v in args.bbox.split(',')] if args.bbox else None
    tiles = tile_list(layer, args.tile_m, bbox)
    svf_source = svf_vrt(out)

    report_path = out/'report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else {
        'resolution_m': args.resolution, 'tile_m': args.tile_m, 'bbox': bbox,
        'wind_placeholder_m_s': WIND_PLACEHOLDER, 'tiles': {}}
    if (report['resolution_m'], report['tile_m']) != (args.resolution, args.tile_m):
        raise SystemExit(f'{out} holds a {report["resolution_m"]} m / {report["tile_m"]} m run; use a new --out folder')
    print(f'{len(tiles)} tiles with land, {len(report["tiles"])} already done', flush=True)

    new = 0
    for col, row, box in tiles:
        name = f't_{col:03d}_{row:03d}.tif'
        path = tiles_dir/name
        if name in report['tiles'] and path.exists():
            continue
        if args.max_tiles and new >= args.max_tiles:
            break
        started = time.perf_counter()
        supported = calculate_tile(box, args.resolution, layer, svf_source, path)
        report['tiles'][name] = {'bounds': [box.left, box.bottom, box.right, box.top],
                                 'seconds': round(time.perf_counter()-started, 1),
                                 'supported_pixels': supported,
                                 'bytes': path.stat().st_size,
                                 'peak_memory_mib': peak_memory_mib()}
        save(report_path, report)
        new += 1
        print(f'{name} ({len(report["tiles"])}/{len(tiles)}): {report["tiles"][name]}', flush=True)

    done = [tiles_dir/name for name in sorted(report['tiles']) if (tiles_dir/name).exists()]
    if done:
        build_vrt(out/'pet.vrt', done)
    rows = report['tiles'].values()
    report['summary'] = {
        'tiles_done': f'{len(report["tiles"])}/{len(tiles)}',
        'seconds': round(sum(t['seconds'] for t in rows)),
        'final_gb': round(sum(t['bytes'] for t in rows)/1e9, 3),
        'supported_km2': round(sum(t['supported_pixels'] for t in rows)*args.resolution**2/1e6, 2),
        'peak_memory_mib': max((t['peak_memory_mib'] for t in rows), default=0),
    }
    save(report_path, report)
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__':
    main()
