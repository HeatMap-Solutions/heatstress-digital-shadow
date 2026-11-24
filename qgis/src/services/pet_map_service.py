"""The PET map, calculated tile by tile (used by tools/generate_pet_map.py).

Every tile is calculated on its own, with a one-grid-cell margin (halo) so the
edges match their neighbours. Intermediate rasters stay in memory and only the
tile's final PET is written, so memory and working disk depend on the tile
size, not on the size of the area. A VRT joins the tiles into one map.

The PET formulas are in src/services/pet_formulas.py, shared with the 3x3 update.
Because the zones are grid cells that line up with the pixels, the zonal means
are done with numpy instead of QGIS vector round trips.

Data paths (sources, Bowen/vegetation, wind): src/configs/area.py

A run folder holds:
    tiles/                          the PET tiles; pet.vrt joins them (this is the map)
    tiles-svf/, tiles-sun-partial/, tiles-shadow-pet/
                                    intermediate layers for the 3x3 update (svf.vrt, sun-partial.vrt, shadow-pet.vrt)
    report.json                     settings and time/size per tile; finished tiles are skipped when a run is resumed
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import resource
import time
import warnings

import numpy as np
from osgeo import gdal, ogr, osr

from src.utils.pet_tiles import TILE_OPTIONS, build_overviews, build_vrt
from src.configs import area
from src.services import pet_formulas as pf

NODATA = -9999.0

# Zone grid: 140 x 280 m cells on a fixed lattice (same cell size and lattice as the 2 m run).
CELL_W, CELL_H = 140, 280
ORIGIN_X, ORIGIN_Y = 2000, 427350   # NW of Zeeland, so tile numbers are positive

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
    manifest = json.loads(area.SVF_MANIFEST.read_text())
    paths = []
    for entry in manifest['files']:
        recorded = entry['local_path'].replace('\\', '/')
        for prefix in ('/data/zeeland/', 'data/zeeland/'):
            if recorded.startswith(prefix):
                recorded = str(area.SOURCE/recorded[len(prefix):])
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


# ---------- per-cell (zonal) part ----------

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


def wind_cells(work, resolution):
    """Wind at 1.2 m (m/s) per zone cell from area.WIND; the placeholder where no calculated value exists."""
    rows = round((work.top-work.bottom)/resolution)
    cols = round((work.right-work.left)/resolution)
    source = ogr.Open(str(area.WIND))
    if source is None:
        raise FileNotFoundError(f'Wind file not found: {area.WIND}')
    layer = source.GetLayerByName(area.WIND_LAYER)
    layer.SetAttributeFilter("quality = 'model_value' AND u_1_2 > 0")   # skip the legacy zeros
    layer.SetSpatialFilterRect(work.left, work.bottom, work.right, work.top)
    target = to_gdal(np.full((rows, cols), np.nan, dtype='float32'), work, resolution)
    gdal.RasterizeLayer(target, [1], layer, options=['ATTRIBUTE=u_1_2'])
    u_factor = target.GetRasterBand(1).ReadAsArray().astype('float32')
    u_factor[u_factor == NODATA] = np.nan
    speed = pf.wind_speed_1_2(u_factor)                                 # geschaalde_u_1_2, as in main
    u = cell_means(speed, resolution)                                   # 250 x 125 m wind cells -> 140 x 280 m zone cells
    source = None
    return np.where(np.isfinite(u) & (u > 0), u, pf.WIND_PLACEHOLDER).astype('float32')


def zonal_partial_pet(vegetation, svf_raw, resolution, u):
    """Per zone cell: air temperature, wet bulb and the partial PET in sun and shade (see pet_formulas)."""
    t_a = pf.air_temperature(cell_means(svf_raw, resolution), cell_means(vegetation, resolution))
    t_w = pf.wet_bulb_temperature(t_a)
    sun, shadow = pf.partial_pet_sun(t_a, t_w, u), pf.partial_pet_shadow(t_a, t_w, u)
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


def calculate_tile(tile, resolution, boundary_layer, svf_source, path):
    """Calculate one tile (plus halo) and write only the tile itself to `path`."""
    work = tile.expand(CELL_W, CELL_H)                    # one grid cell of context on every side
    dsm_resolution = resolution / 2                       # elevation at twice the PET detail, as main and the 2 m run do
    classes = warp(area.LGN, work, resolution, 'near', gdal.GDT_Int16)
    land = np.isfinite(classes) & (classes > 0)                                             # LGN: only the land mask
    bowen = np.where(land, warp(area.BOWEN, work, resolution, 'near'), np.nan).astype('float32')
    vegetation = np.where(land, warp(area.VEGETATION, work, resolution, 'average'), np.nan).astype('float32')
    svf_raw = warp(svf_source, work, resolution, 'average')
    svf = fill_nodata(svf_raw, work, resolution, distance_px=max(1, round(10/resolution)))  # 10 m search
    dsm = warp(area.DSM, work, dsm_resolution, 'average')

    sun_partial, shadow_partial, t_a = zonal_partial_pet(vegetation, svf_raw, resolution, wind_cells(work, resolution))
    sun_pet = pf.total_pet_sun(sun_partial, bowen, svf)
    shadow_pet = pf.total_pet_shadow(shadow_partial, svf, t_a)
    shade = pf.hillshade(to_gdal(dsm, work, dsm_resolution), work, resolution)
    pet = pf.combine(sun_pet, shadow_pet, shade)

    # Keep only the tile (drop the halo) and mask what is not supported land in Zeeland.
    hx, hy = round(CELL_W/resolution), round(CELL_H/resolution)
    core = (slice(hy, pet.shape[0]-hy), slice(hx, pet.shape[1]-hx))
    keep = (boundary_mask(boundary_layer, tile, resolution) & land[core]
        & np.isfinite(pet[core]) & (svf[core] >= 0) & (svf[core] <= 1))
    result = np.where(keep, pet[core], np.nan).astype('float32')
    # Intermediate layers for the 3x3 update (local_pet_service.py), on the same grid as the PET tile.
    # Written before the PET tile, so a finished PET tile always has its three intermediate tiles.
    for name, values in (('svf', svf), ('sun-partial', sun_partial), ('shadow-pet', shadow_pet)):
        extra = path.parent.parent/f'tiles-{name}'/path.name
        extra.parent.mkdir(exist_ok=True)
        ds = to_gdal(values[core].astype('float32'), tile, resolution, path=extra, options=TILE_OPTIONS)
        ds = None
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


def generate(out, resolution, tile_m=2800, bbox=None, max_tiles=0):
    """Calculate a PET run in the folder `out`, or resume it; returns the summary that is also in report.json.

    resolution: PET pixel size in metres (1 or 2). tile_m: tile size in metres (multiple of 280).
    bbox: (left, bottom, right, top) in RD New metres, only tiles touching it; None for the whole area.
    max_tiles: stop after this many new tiles (a timing sample); 0 = no limit.
    """
    if resolution not in (1, 2):
        raise ValueError('resolution must be 1 or 2 (metres)')
    if tile_m % CELL_H or tile_m % CELL_W:
        raise ValueError('tile_m must be a multiple of 140 and 280, e.g. 2800')
    gdal.UseExceptions()
    gdal.SetCacheMax(256*1024**2)

    out = Path(out)
    tiles_dir = out/'tiles'
    tiles_dir.mkdir(parents=True, exist_ok=True)
    boundary = ogr.Open(str(area.BOUNDARY))
    layer = boundary.GetLayer()
    bbox = list(bbox) if bbox else None
    tiles = tile_list(layer, tile_m, bbox)
    svf_source = svf_vrt(out)

    report_path = out/'report.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else {
        'resolution_m': resolution, 'tile_m': tile_m, 'bbox': bbox,
        'wind_placeholder_m_s': pf.WIND_PLACEHOLDER, 'wind_source': str(area.WIND),
        'bowen_source': str(area.BOWEN), 'vegetation_source': str(area.VEGETATION), 'tiles': {}}
    if (report['resolution_m'], report['tile_m']) != (resolution, tile_m):
        raise ValueError(f'{out} holds a {report["resolution_m"]} m / {report["tile_m"]} m run; use a new folder')
    print(f'{len(tiles)} tiles with land, {len(report["tiles"])} already done', flush=True)

    new = 0
    for col, row, box in tiles:
        name = f't_{col:03d}_{row:03d}.tif'
        path = tiles_dir/name
        if name in report['tiles'] and path.exists():
            continue
        if max_tiles and new >= max_tiles:
            break
        started = time.perf_counter()
        supported = calculate_tile(box, resolution, layer, svf_source, path)
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
        for name in ('svf', 'sun-partial', 'shadow-pet'):      # inputs for the 3x3 update
            extra = [out/f'tiles-{name}'/p.name for p in done if (out/f'tiles-{name}'/p.name).exists()]
            if extra:
                build_vrt(out/f'{name}.vrt', extra)
    rows = report['tiles'].values()
    report['summary'] = {
        'tiles_done': f'{len(report["tiles"])}/{len(tiles)}',
        'seconds': round(sum(t['seconds'] for t in rows)),
        'final_gb': round(sum(t['bytes'] for t in rows)/1e9, 3),
        'supported_km2': round(sum(t['supported_pixels'] for t in rows)*resolution**2/1e6, 2),
        'peak_memory_mib': max((t['peak_memory_mib'] for t in rows), default=0),
    }
    save(report_path, report)
    return report['summary']