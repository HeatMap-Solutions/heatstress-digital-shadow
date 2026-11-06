#!/usr/bin/env python3
"""Prepare bounded Walcheren working rasters from retained immutable sources.

Run in the project's QGIS image with sources mounted read-only at /data and
the dedicated /data/zeeland/walcheren-pilot directory writable.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.utils.storage_budget import check_storage_budget

import numpy as np
from osgeo import gdal, ogr, osr

ROOT = Path('/data/zeeland')
OUT = Path(os.environ.get('REGION_ROOT', os.environ.get('WALCHEREN_ROOT',str(ROOT/'walcheren-pilot'))))
RESOLUTION = float(os.environ.get('PET_RESOLUTION_M', '1'))
ELEVATION_STORAGE = os.environ.get('ELEVATION_STORAGE', 'materialized')
if ELEVATION_STORAGE not in ('materialized', 'virtual'):
    raise ValueError('ELEVATION_STORAGE must be materialized or virtual')
if RESOLUTION not in (1, 2, 5, 10):
    raise ValueError('PET_RESOLUTION_M must be 1, 2, 5 or 10')
OPTIONS = ['TILED=YES', 'COMPRESS=DEFLATE', 'PREDICTOR=3', 'BIGTIFF=IF_SAFER']
NODATA = -9999
PAVED = [18, 19, 24, 25, 26, 27]


def svf_sources(data_root=Path('/data')):
    """Use recorded local paths, including tiles reused from earlier downloads."""
    manifest = data_root/'zeeland/sources/svf-nl-3/manifest.json'
    entries = json.loads(manifest.read_text())['files']
    paths = []
    for entry in entries:
        relative = Path(entry['local_path'])
        path = relative if relative.is_absolute() else data_root/relative.relative_to('data')
        if not path.is_file() or path.stat().st_size != entry['bytes']:
            raise ValueError(f'Missing or changed recorded SVF source: {path}')
        paths.append(str(path))
    if not paths or len(paths) != len(set(paths)):
        raise ValueError('SVF manifest must contain unique source paths')
    return paths


def valid(band, values, y=0):
    result = np.isfinite(values)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        result &= values != nodata
    return result & (band.GetMaskBand().ReadAsArray(0, y, values.shape[1], values.shape[0]) != 0)


def coarse_elevation(name, extent, prepared):
    """Read each compressed archive sequentially once, with one extracted tile."""
    sources = json.loads((ROOT/f'acquisition-20261005/elevation-{name}/manifest.json').read_text())['sources']
    cache = prepared/f'{name}-coarse-tiles'
    cache.mkdir(exist_ok=True)
    def reduce_tile(item):
        t = item['transform']
        cols, rows = item['shape']
        bounds = [t[0],t[3]+rows*t[5],t[0]+cols*t[1],t[3]]
        if bounds[2] <= extent[0] or bounds[0] >= extent[2] or bounds[3] <= extent[1] or bounds[1] >= extent[3]:
            return None
        target = cache/(Path(item['member']).stem+'.tif')
        if not target.exists():
            check_storage_budget(OUT, 2*(item['expanded_bytes']+32*1024**2))
            with tempfile.TemporaryDirectory(prefix='source-',dir=prepared) as scratch:
                extracted = Path(scratch)/'source.tif'
                with zipfile.ZipFile(item['archive']) as archive, archive.open(item['member']) as source, extracted.open('wb') as dest:
                    shutil.copyfileobj(source,dest,1024*1024)
                partial = target.with_suffix('.partial.tif')
                elevation_resolution = 1 if RESOLUTION == 2 else RESOLUTION
                ds = gdal.Warp(str(partial),str(extracted),xRes=elevation_resolution,yRes=elevation_resolution,
                    dstSRS='EPSG:28992',resampleAlg='average',dstNodata=NODATA,
                    outputType=gdal.GDT_Float32,creationOptions=OPTIONS,
                    warpMemoryLimit=128*1024**2)
                ds = None
                partial.replace(target)
            print(f'{name} coarse tile: {target.name}',flush=True)
        return str(target)
    # Two fresh GDAL datasets and private scratch directories, at most two
    # extracted tiles. No QGIS objects or mutable GDAL datasets are shared.
    with ThreadPoolExecutor(max_workers=2) as workers:
        paths = [path for path in workers.map(reduce_tile,sources) if path is not None]
    output = prepared/f'{name}-coarse.vrt'
    ds = gdal.BuildVRT(str(output),paths)
    ds = None
    return str(output)


def main():
    check_storage_budget(OUT)
    gdal.UseExceptions()
    ogr.UseExceptions()
    osr.UseExceptions()
    gdal.SetCacheMax(256 * 1024**2)
    gdal.SetConfigOption('GDAL_PAM_ENABLED', 'NO')
    boundary = OUT / 'boundary/region-rd.gpkg'
    if not boundary.exists():
        boundary = OUT / 'boundary/walcheren-rd.gpkg'
    vector = ogr.Open(str(boundary))
    layer = vector.GetLayer()
    expected = osr.SpatialReference()
    expected.ImportFromEPSG(28992)
    if not layer.GetSpatialRef().IsSame(expected):
        raise ValueError('Walcheren boundary must use EPSG:28992')
    bounds = json.loads((OUT / 'boundary/source.json').read_text())['extent_wsen']
    w, s, e, n = bounds
    lgn = f'/vsizip/{ROOT}/sources/lgn2018/LGN2018.zip/LGN2018/LGN2018.tif'
    # Virtual source plus disk mask: inspect bounded rows, never a whole province array.
    classes = gdal.Warp('', lgn, format='VRT', outputBounds=bounds,
                        xRes=5, yRes=5, dstSRS='EPSG:28992', resampleAlg='near',
                        dstNodata=NODATA, outputType=gdal.GDT_Int16)
    band = classes.GetRasterBand(1)
    audit_path = OUT/'boundary/audit-mask.partial.tif'
    mask = gdal.GetDriverByName('GTiff').Create(str(audit_path), classes.RasterXSize,
                    classes.RasterYSize, 1, gdal.GDT_Byte,
                    options=['TILED=YES','COMPRESS=DEFLATE'])
    mask.SetGeoTransform(classes.GetGeoTransform())
    mask.SetProjection(classes.GetProjection())
    gdal.RasterizeLayer(mask, [1], layer, burn_values=[1])
    land_count = water_count = missing_count = 0
    min_row, min_col = classes.RasterYSize, classes.RasterXSize
    max_row = max_col = -1
    for y in range(0, classes.RasterYSize, 128):
        h = min(128, classes.RasterYSize-y)
        values = band.ReadAsArray(0,y,classes.RasterXSize,h)
        inside = mask.ReadAsArray(0,y,classes.RasterXSize,h).astype(bool)
        supported = valid(band,values,y) & (values > 0)
        water = np.isin(values,[16,17])
        land = inside & supported & ~water
        land_count += int(land.sum())
        water_count += int((inside & supported & water).sum())
        missing_count += int((inside & ~supported).sum())
        rows, cols = np.nonzero(land)
        if len(rows):
            min_row, max_row = min(min_row,y+rows.min()), max(max_row,y+rows.max())
            min_col, max_col = min(min_col,cols.min()), max(max_col,cols.max())
    mask = classes = band = None
    audit_path.unlink()
    if not land_count:
        raise ValueError('No LGN land within the regional boundary')
    # Retain a whole-cell margin around land for preprocessing and zonal means.
    west = 30000 + np.floor((w + min_col*5 - 140 - 30000)/140)*140
    east = 30000 + np.ceil((w + (max_col+1)*5 + 140 - 30000)/140)*140
    north = 393750 - np.floor((393750 - (n - min_row*5 + 280))/280)*280
    south = 393750 - np.ceil((393750 - (n - (max_row+1)*5 - 280))/280)*280
    extent = [float(west), float(south), float(east), float(north)]
    report = {'municipality_extent_wsen': bounds, 'processing_extent_wsen': extent,
              'lgn_sample_resolution_m': 5, 'pet_resolution_m': RESOLUTION, 'land_sample_pixels': land_count,
              'water_sample_pixels': water_count,
              'missing_lgn_sample_pixels': missing_count,
              'water_classes': [16, 17], 'gdal': gdal.VersionInfo(),
              'boundary_sha256': hashlib.sha256(boundary.read_bytes()).hexdigest(),
              'elevation_storage': ELEVATION_STORAGE,
              'scope': 'LGN2018 support audit; not a PET map or scientific validation',
              'stages': {}}
    prepared = OUT / 'prepared'
    prepared.mkdir(exist_ok=True)
    if shutil.disk_usage(prepared).free < 20*1024**3:
        raise ValueError('Preparation requires 20 GiB free including reserve')
    manifest = prepared / 'manifest.json'
    if manifest.exists():
        previous = json.loads(manifest.read_text())
        if (previous['processing_extent_wsen'] != extent or previous.get('pet_resolution_m', 1) != RESOLUTION
                or previous.get('elevation_storage', 'materialized') != ELEVATION_STORAGE):
            raise ValueError('Prepared extent changed; use a new output directory')
        report['stages'] = previous['stages']
    manifest.write_text(json.dumps(report, indent=2)+'\n')
    sources = {key: str(ROOT / f'acquisition-20261005/elevation-{key}/native.vrt')
               for key in ('dsm', 'dtm')}
    svf_vrt = prepared/'svf-sources.vrt'
    paths = svf_sources(ROOT.parent)
    report['svf_source_count'] = len(paths)
    report['svf_manifest_sha256'] = hashlib.sha256((ROOT/'sources/svf-nl-3/manifest.json').read_bytes()).hexdigest()
    if svf_vrt.exists():
        mosaic = gdal.Open(str(svf_vrt))
        cached = {str(Path(p).resolve()) for p in mosaic.GetFileList()}-{str(svf_vrt.resolve())}
        mosaic = None
        if cached != set(paths):
            raise ValueError('Cached SVF mosaic differs from the recorded sources; prepare a new output revision')
    if not svf_vrt.exists():
        mosaic = gdal.BuildVRT(str(svf_vrt),paths)
        if mosaic is None:
            raise RuntimeError('Could not create the complete SVF mosaic')
        mosaic = None
    manifest.write_text(json.dumps(report,indent=2)+'\n')
    sources.update(svf=str(svf_vrt), classes=lgn)
    for name, source in sources.items():
        virtual = ELEVATION_STORAGE == 'virtual' and name in ('dsm', 'dtm')
        target = prepared/f'{name}.{"vrt" if virtual else "tif"}'
        if target.exists() and name in report['stages']:
            continue
        if target.exists():
            raise ValueError(f'Unrecorded output: {target}')
        # Native elevation has four times as many pixels as the 1 m PET grid.
        resolution = ((.5 if RESOLUTION == 1 else 1 if RESOLUTION == 2 else RESOLUTION)
                      if name in ('dsm', 'dtm') else RESOLUTION)
        check_storage_budget(OUT, 1024**2 if virtual else round((east-west)*(north-south)/resolution**2)*4)
        started = time.perf_counter()
        if RESOLUTION > 1 and name in ('dsm','dtm') and not virtual:
            source = coarse_elevation(name,extent,prepared)
        temporary = target.with_name(f'{name}.partial.{target.suffix[1:]}')
        # Elevation retains native resolution for the unchanged wind/hillshade.
        resampling = 'average' if RESOLUTION > 1 and name != 'classes' else 'near'
        options = OPTIONS if name != 'classes' else [
            'TILED=YES', 'COMPRESS=DEFLATE', 'BIGTIFF=IF_SAFER']
        ds = gdal.Warp(str(temporary), source, outputBounds=extent,
                       format='VRT' if virtual else 'GTiff',
                       xRes=resolution, yRes=resolution, dstSRS='EPSG:28992',
                       resampleAlg=resampling, dstNodata=NODATA,
                       outputType=gdal.GDT_Int16 if name == 'classes' else gdal.GDT_Float32,
                       creationOptions=None if virtual else options, warpMemoryLimit=128*1024**2)
        if ds is None or ds.RasterXSize != round((east-west)/resolution):
            raise RuntimeError(f'Invalid {name} output grid')
        ds = None
        temporary.replace(target)
        report['stages'][name] = {'source': source, 'resolution_m': resolution,
                                 'resampling': resampling, 'bytes': target.stat().st_size,
                                 'seconds': time.perf_counter()-started, 'storage': 'virtual' if virtual else 'materialized'}
        manifest.write_text(json.dumps(report, indent=2)+'\n')
        print(f'{name} prepared: {report["stages"][name]}', flush=True)
    regional_mask = prepared/'region-mask.tif'
    ds = gdal.Rasterize(str(regional_mask), str(boundary), outputBounds=extent,
        xRes=RESOLUTION, yRes=RESOLUTION, burnValues=[1], outputType=gdal.GDT_Byte,
        creationOptions=['TILED=YES','COMPRESS=DEFLATE'])
    ds = None
    report['regional_mask'] = str(regional_mask)
    manifest.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
