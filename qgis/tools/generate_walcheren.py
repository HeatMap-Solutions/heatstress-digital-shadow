#!/usr/bin/env python3
"""Generate the Walcheren experimental base map using existing PET equations.

Uses cached BAG/BGT, bounded wind windows with one-cell context, native AHN3,
LGN2018 and SVF_NL/3. Undefined wind uses the developer's explicit 7.5 m/s
pedestrian-wind assumption, AFTER scaling. Original sources are never edited.
"""
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import gc
import hashlib
import json
import math
import os
import resource
import subprocess
from pathlib import Path
import shutil
import sys
import tempfile
import time

import numpy as np
from osgeo import gdal, ogr, osr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.configs.preflight import init_qgis
from src.utils.storage_budget import check_storage_budget
from src.utils.raster_sources import elevation_path, copy_virtual_raster
from src.services.geojson_service import GeoJSONService
from src.services.pet_service import PETService
from src.services.raster_service import RasterService
from src.services.shadow_service import ShadowService
from src.services.wind.wind_service import WindService
from src.utils.layer_utils import load_vector_layer, export_layer_to_geopackage
from qgis.core import (QgsProject, QgsRasterLayer, QgsVectorLayer, QgsReferencedRectangle,
    QgsFeature, Qgis, QgsRasterShader, QgsColorRampShader, QgsSingleBandPseudoColorRenderer,
    QgsSymbol, QgsRendererRange, QgsGraduatedSymbolRenderer)
from qgis.PyQt.QtGui import QColor
from qgis.analysis import QgsZonalStatistics

ROOT = Path(os.environ.get('REGION_ROOT', os.environ.get('WALCHEREN_ROOT','/data/zeeland/walcheren-pilot')))
RESOLUTION = float(os.environ.get('PET_RESOLUTION_M', '1'))
if RESOLUTION not in (1, 2, 5, 10):
    raise ValueError('PET_RESOLUTION_M must be 1, 2, 5 or 10')
BOUNDARY = ROOT/'boundary/region-rd.gpkg'
if not BOUNDARY.exists():
    BOUNDARY = ROOT/'boundary/walcheren-rd.gpkg'
APP = ROOT/'app-data'
PREP = ROOT/'prepared'
NODATA = -9999
FALLBACK = 7.5
OPTIONS = ['TILED=YES', 'COMPRESS=DEFLATE', 'PREDICTOR=3', 'BIGTIFF=IF_SAFER']


def save(path, value):
    temporary = path.with_suffix('.partial.json')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def discard_stage_temporaries(stage_name, state):
    """Opt-in removal only after downstream stage checkpoints are saved.

    These products are not scenario inputs. Keep prepared sources, published
    layers, partial sun PET, shade PET and all checkpoint/evidence records.
    """
    if os.environ.get('KEEP_INTERMEDIATE_MAPS', '1') != '0':
        return
    obsolete = {
        'shadow': ('uhi/shadow-bbox.tif', 'uhi/t_a.tif'),
        'combine': ('uhi/sun-pet.tif', 'shadow-maps/shadow_map_aligned.tif',
                    'shadow-maps/hillshade_20150701_1500.tif'),
        'support_mask': ('pet/combined.tif',),
    }
    if stage_name not in state['stages']:
        raise ValueError('Cannot remove intermediates before the consuming stage completed')
    removed = state.setdefault('discarded_intermediates', {})
    for relative in obsolete[stage_name]:
        path = APP/relative
        if path.is_file():
            size = path.stat().st_size
            path.unlink()
            removed[relative] = {'bytes': size, 'after_stage': stage_name}
    save(ROOT/'generation.json', state)


def create(path, bounds, dtype=gdal.GDT_Float32, nodata=NODATA):
    w, s, e, n = bounds
    ds = gdal.GetDriverByName('GTiff').Create(
        str(path), round((e-w)/RESOLUTION), round((n-s)/RESOLUTION), 1, dtype,
        options=OPTIONS if dtype == gdal.GDT_Float32 else OPTIONS[:2]+OPTIONS[3:])
    ds.SetGeoTransform((w, RESOLUTION, 0, n, 0, -RESOLUTION))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(28992)
    ds.SetProjection(crs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(nodata)
    ds.GetRasterBand(1).Fill(nodata)
    return ds


def valid(band, values):
    nd = band.GetNoDataValue()
    return np.isfinite(values) & (values != nd if nd is not None else True)


def diagnostic_raster(path,name,stops):
    layer = QgsRasterLayer(str(path),name)
    if not layer.isValid():
        raise RuntimeError(f'Invalid diagnostic raster: {path}')
    low,high = stops[0][0],stops[-1][0]
    ramp = QgsColorRampShader(low,high)
    ramp.setColorRampType(Qgis.ShaderInterpolationMethod.Linear)
    ramp.setColorRampItemList([QgsColorRampShader.ColorRampItem(value,QColor(color),str(value))
                              for value,color in stops])
    shader = QgsRasterShader(low,high)
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(),1,shader)
    renderer.setClassificationMin(low)
    renderer.setClassificationMax(high)
    layer.setRenderer(renderer)
    return layer


def diagnostic_wind(path,name,field,edges):
    layer = QgsVectorLayer(str(path),name,'ogr')
    if not layer.isValid():
        raise RuntimeError(f'Invalid diagnostic wind: {path}')
    colors = ['#2166ac','#67a9cf','#d1e5f0','#fddbc7','#ef8a62','#b2182b']
    ranges = []
    for low,high,color in zip(edges,edges[1:],colors):
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(color))
        symbol.symbolLayer(0).setStrokeColor(QColor(color))
        ranges.append(QgsRendererRange(low,high,symbol,f'{low} - {high} m/s'))
    layer.setRenderer(QgsGraduatedSymbolRenderer(field,ranges))
    return layer


class CachedVectors:
    """The existing wind pipeline receives local geometry within its context."""
    def __init__(self, sources):
        self.sources = sources

    def _import(self, name, output, extent):
        gdal.VectorTranslate(str(output), str(self.sources[name]), format='GPKG',
                             spatFilter=[extent.xMinimum(), extent.yMinimum(),
                                         extent.xMaximum(), extent.yMaximum()])
        return {'path': str(output), 'layer': load_vector_layer(str(output), name)}

    def import_buildings(self, output_geopackage_path, extent):
        return self._import('buildings', output_geopackage_path, extent)

    def import_trees(self, output_geopackage_path, extent):
        return self._import('trees', output_geopackage_path, extent)


def prepare_vectors():
    paths = {}
    cache = Path('/data/zeeland/acquisition-20261005/vectors-province')
    for name in ('buildings', 'trees'):
        target = ROOT/f'{name}.gpkg'
        if not target.exists():
            options = {'where': "status NOT IN ('Pand gesloopt', 'Niet gerealiseerd pand', 'Pand ten onrechte opgevoerd')"} if name == 'buildings' else {}
            gdal.VectorTranslate(str(target), str(cache/f'{name}.geojson'), format='GPKG', **options)
        paths[name] = target
    return paths


def prepare_thematic(bounds):
    classes = gdal.Open(str(PREP/'classes.tif'))
    products = [create(APP/'raster/br-reproject.tif', bounds),
                create(APP/'raster/vegetation-reproject.tif', bounds)]
    band = classes.GetRasterBand(1)
    for y in range(0, classes.RasterYSize, 128):
        values = band.ReadAsArray(0, y, classes.RasterXSize, min(128, classes.RasterYSize-y))
        keep = valid(band, values) & (values > 0)
        paved = np.isin(values, [18,19,24,25,26,27])
        for ds, mapped in zip(products, (np.where(paved, 3., .4), (~paved).astype(float))):
            ds.GetRasterBand(1).WriteArray(np.where(keep, mapped, NODATA).astype('float32'), 0, y)
    for ds in products:
        ds.FlushCache()
    ds = None
    products.clear()


def calculate_wind_window(context, vectors, destination):
    """The existing wind calculation on one bounded native-resolution window."""
    with tempfile.TemporaryDirectory(prefix='wind-', dir=destination.parent) as temporary:
        folder = Path(temporary)
        paths = {}
        for name in ('dsm', 'dtm'):
            path = folder/f'{name}.tif'
            ds = gdal.Translate(str(path), str(elevation_path(PREP, name)),
                projWin=[context[0],context[3],context[2],context[1]], creationOptions=OPTIONS)
            if ds is None:
                raise RuntimeError(f'Failed native wind crop: {name}')
            ds = None
            paths[name] = str(path)
        service = WindService()
        service.wfs = CachedVectors(vectors)
        result = service.generate_wind_reduction_map(paths['dsm'], paths['dtm'],
            str(folder/'run'), wind_direction='west', grid_cell_width=140,
            grid_cell_height=280, raster_resolution=RESOLUTION)
        partial = destination.with_suffix('.partial.gpkg')
        shutil.copyfile(result['outputs']['wind_grid'], partial)
        partial.replace(destination)
        result = service = None
        gc.collect()


def isolated_wind_window(context, vectors, destination):
    """Release all native GIS allocations by exiting after each window."""
    with tempfile.TemporaryDirectory(prefix='worker-', dir=destination.parent) as temporary:
        folder = Path(temporary)
        job = folder/'job.json'
        save(job, {'context':context, 'vectors':{k:str(v) for k,v in vectors.items()},
                   'destination':str(destination)})
        log = folder/'worker.log'
        with log.open('wb') as output:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                '--wind-window', str(job)], stdout=output, stderr=subprocess.STDOUT,
                env={**os.environ, 'TMPDIR':str(folder), 'CPL_TMPDIR':str(folder)})
        if result.returncode:
            with log.open('rb') as output:
                output.seek(max(0, log.stat().st_size-8192))
                tail = output.read().decode('utf-8',errors='replace')
            raise RuntimeError(f'Wind worker exited {result.returncode}: {tail}')
        if not destination.is_file():
            raise RuntimeError('Wind worker returned without publishing its window')


def wind_grid(bounds, state, vectors):
    w, s, e, n = bounds
    cols, rows = round((e-w)/140), round((n-s)/280)
    # Every region has a consistent lattice, including cells later masked as water.
    completed = state.setdefault('wind_tiles', {})
    output = ROOT/'wind-tiles'
    output.mkdir(exist_ok=True)
    classes = gdal.Open(str(PREP/'classes.tif'))
    regional_mask = gdal.Open(str(PREP/'region-mask.tif')) if (PREP/'region-mask.tif').exists() else None
    workers = int(os.environ.get('WIND_WORKERS','1'))
    if workers not in (1,2):
        raise ValueError('WIND_WORKERS must be 1 or 2')
    isolated = os.environ.get('FRESH_WIND_PROCESS') == '1'
    if workers > 1 and not isolated:
        raise ValueError('Parallel wind requires isolated GIS processes')
    action = isolated_wind_window if isolated else calculate_wind_window
    def record(label,core,destination,started):
        completed[label] = {'status':'generated','core':core,'path':str(destination),
                            'seconds':time.perf_counter()-started,'sha256':digest(destination)}
        save(ROOT/'generation.json',state)
        print(f'Wind tile {label}: {completed[label]["seconds"]:.1f}s',flush=True)
    pending = {}
    def collect():
        done,_ = wait(pending,return_when=FIRST_COMPLETED)
        error = None
        for future in done:
            job = pending.pop(future)
            try:
                future.result()
            except Exception as exc:
                error = exc
            else:
                record(*job)
        if error is not None:
            raise error
    # Threads launch/wait on fresh child processes only. All raster datasets,
    # QGIS objects, checkpoint writes and assembly remain in the parent thread.
    # The default direct QGIS calculation stays on its initializing thread.
    pool = ThreadPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        for row in range(0, rows, 10):
            for col in range(0, cols, 20):
                label = f'{col}-{row}'
                if label in completed:
                    saved = completed[label]
                    if saved['status'] == 'generated' and digest(Path(saved['path'])) != saved['sha256']:
                        raise RuntimeError(f'Changed wind checkpoint: {label}')
                    continue
                if shutil.disk_usage(ROOT).free < 10*1024**3:
                    raise RuntimeError('Reached the 10 GiB free-space reserve')
                cw, ch = min(20, cols-col), min(10, rows-row)
                core = [w+col*140, n-(row+ch)*280, w+(col+cw)*140, n-row*280]
                a = classes.ReadAsArray(round(col*140/RESOLUTION), round(row*280/RESOLUTION),
                                        round(cw*140/RESOLUTION), round(ch*280/RESOLUTION))
                land = valid(classes.GetRasterBand(1), a) & (a > 0) & ~np.isin(a, [16,17])
                if regional_mask is not None:
                    inside = regional_mask.ReadAsArray(round(col*140/RESOLUTION), round(row*280/RESOLUTION),
                                        round(cw*140/RESOLUTION), round(ch*280/RESOLUTION))
                    land &= inside == 1
                if not land.any():
                    completed[label] = {'status': 'water_or_missing_source', 'core': core}
                    save(ROOT/'generation.json', state)
                    continue
                context = [max(w, core[0]-140), max(s, core[1]-280),
                           min(e, core[2]+140), min(n, core[3]+280)]
                check_storage_budget(ROOT, workers*256*1024**2)
                started = time.perf_counter()
                destination = output/f'{label}.gpkg'
                if pool is None:
                    action(context,vectors,destination)
                    record(label,core,destination,started)
                else:
                    pending[pool.submit(action,context,vectors,destination)] = (label,core,destination,started)
                    if len(pending) >= workers:
                        collect()
        while pending:
            collect()
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
    generated = {}
    for item in completed.values():
        if item['status'] != 'generated':
            continue
        ds = ogr.Open(item['path'])
        cw, cs, ce, cn = item['core']
        for f in ds.GetLayer():
            left, right, bottom, top = f.GetGeometryRef().GetEnvelope()
            if cw <= left < ce and cs < top <= cn:
                key = (round((left-w)/140), round((n-top)/280))
                generated[key] = json.loads(f.ExportToJson())['properties']
    features = []
    for col in range(cols):
        for row in range(rows):
            left, top = w+col*140, n-row*280
            right, bottom = left+140, top-280
            p = generated.get((col,row), {})
            fields = ('lambda_total', 'u_h', 'u_1_2')
            available = all(isinstance(p.get(k), (int,float)) and math.isfinite(p[k]) for k in fields)
            properties = {'col_index': col, 'row_index': row, 'left': left, 'right': right,
                          'bottom': bottom, 'top': top, 'u_1_2': p.get('u_1_2') if available else 0,
                          'wind_fallback': int(not available), 'wind_assumption_m_s': FALLBACK if not available else None}
            features.append({'type':'Feature', 'properties':properties,
                'geometry':{'type':'Polygon','coordinates':[[[left,top],[right,top],
                    [right,bottom],[left,bottom],[left,top]]]}})
    path = APP/'server/wind-speed-calc.geojson'
    save(path, {'type':'FeatureCollection', 'crs':{'type':'name','properties':{
        'name':'urn:ogc:def:crs:EPSG::28992'}}, 'features':features})
    state['wind_cells'] = {'total':len(features), 'fallback':sum(f['properties']['wind_fallback'] for f in features)}
    return path


def calculate_zones(grid, bounds):
    source_zones = QgsVectorLayer(str(grid), 'Walcheren wind', 'ogr')
    if not source_zones.isValid() or source_zones.crs().authid() != 'EPSG:28992':
        raise ValueError('Regional wind grid must declare EPSG:28992')
    zones = QgsVectorLayer('Polygon?crs=EPSG:28992','private PET zones','memory')
    provider = zones.dataProvider()
    provider.addAttributes(source_zones.fields())
    zones.updateFields()
    for source_feature in source_zones.getFeatures():
        item = QgsFeature(zones.fields())
        item.setGeometry(source_feature.geometry())
        item.setAttributes(source_feature.attributes())
        provider.addFeature(item)
    zones.updateExtents()
    # Exclude unsupported cells before passing them into the legacy equations.
    # QGIS NULL values are not Python None in every installed version.
    for source, prefix in [(APP/'raster/vegetation-reproject.tif','available_veg_'),
                           (PREP/'svf.tif','available_svf_')]:
        raster_layer = QgsRasterLayer(str(source))
        statistics = QgsZonalStatistics(zones, raster_layer, attributePrefix=prefix,
                                        stats=QgsZonalStatistics.Mean)
        if statistics.calculateStatistics(None) != 0:
            raise RuntimeError(f'Failed zonal coverage statistics: {source}')
    zones.startEditing()
    for feature in zones.getFeatures():
        if not all(isinstance(feature[k],(int,float)) and math.isfinite(feature[k])
                   for k in ('available_veg_mean','available_svf_mean')):
            zones.deleteFeature(feature.id())
    zones.commitChanges()
    pet = PETService()
    GeoJSONService().calculate_wind_speed_1_2(zones)
    zones.startEditing()
    for f in zones.getFeatures():
        if f['wind_fallback']:
            f['geschaalde_u_1_2'] = FALLBACK
            # Raw morphology wind is unavailable; preserve that fact in WMS.
            f['u_1_2'] = None
            zones.updateFeature(f)
    zones.commitChanges()
    pet.calculate_zonal_uhi(zones, str(APP/'raster/vegetation-reproject.tif'), str(PREP/'svf.tif'))
    pet.calculate_t_a_temperature(zones)
    pet.calculate_wet_bulb_temp(zones)
    pet.calculate_zonal_part_pet_sun(zones, u_field='geschaalde_u_1_2')
    pet.calculate_zonal_part_pet_shadow(zones, u_field='geschaalde_u_1_2')
    # Missing zonal inputs remain NoData; skip fields that QGIS evaluates as NULL.
    zone_path = APP/'server/pet-zones.gpkg'
    export_layer_to_geopackage(zones,str(zone_path))
    data = ogr.Open(str(zone_path))
    layer = data.GetLayer()
    for name, field in [('uhi/sun-bbox.tif','pet_sun_partial'),
                        ('uhi/shadow-bbox.tif','pet_shadow_partial'), ('uhi/t_a.tif','t_a')]:
        target = create(APP/name, bounds)
        gdal.RasterizeLayer(target, [1], layer, options=[f'ATTRIBUTE={field}'])
        target = None
    zones = data = None


def mask_pet(bounds, raw):
    classes = gdal.Open(str(PREP/'classes.tif'))
    svf = gdal.Open(str(APP/'raster/svf-reproject-filled.tif'))
    dsm = gdal.Warp('', str(elevation_path(PREP, 'dsm')), format='VRT', outputBounds=bounds,
                    xRes=RESOLUTION,yRes=RESOLUTION,resampleAlg='near',dstNodata=NODATA)
    boundary_path = ROOT/'boundary/region-rd.gpkg'
    if not boundary_path.exists():
        boundary_path = ROOT/'boundary/walcheren-rd.gpkg'
    vector = ogr.Open(str(boundary_path))
    mask = create(ROOT/'boundary-mask.tif', bounds, gdal.GDT_Byte, 0)
    gdal.RasterizeLayer(mask, [1], vector.GetLayer(), burn_values=[1])
    source = gdal.Open(str(raw))
    support = create(APP/'raster/pet-support.tif', bounds, gdal.GDT_Byte, 0)
    fallback = create(APP/'raster/wind-fallback.tif', bounds, gdal.GDT_Byte, 0)
    wind = ogr.Open(str(APP/'server/wind-speed-calc.geojson'))
    gdal.RasterizeLayer(fallback,[1],wind.GetLayer(),options=['ATTRIBUTE=wind_fallback'])
    target = create(APP/'server/pet-version-1.partial.tif', bounds)
    counts = {'supported_pixels':0,'excluded_pixels':0,'land_pixels_lgn':0,
              'supported_fallback_pixels':0,'missing_lgn_pixels':0,
              'min_pet':math.inf,'max_pet':-math.inf}
    for y in range(0, target.RasterYSize, 128):
        h = min(128, target.RasterYSize-y)
        arrays = [ds.ReadAsArray(0,y,target.RasterXSize,h) for ds in (classes,svf,dsm,mask,source)]
        c,v,d,m,p = arrays
        keep = (m == 1) & valid(classes.GetRasterBand(1),c) & (c > 0) & ~np.isin(c,[16,17])
        counts['land_pixels_lgn'] += int(keep.sum())
        counts['missing_lgn_pixels'] += int(((m == 1) & ~valid(classes.GetRasterBand(1),c)).sum())
        keep &= valid(svf.GetRasterBand(1),v) & (v >= 0) & (v <= 1)
        keep &= valid(dsm.GetRasterBand(1),d) & valid(source.GetRasterBand(1),p)
        target.GetRasterBand(1).WriteArray(np.where(keep,p,NODATA).astype('float32'),0,y)
        support.GetRasterBand(1).WriteArray(keep.astype('uint8'),0,y)
        counts['supported_pixels'] += int(keep.sum())
        counts['supported_fallback_pixels'] += int((keep & (fallback.ReadAsArray(0,y,target.RasterXSize,h) == 1)).sum())
        counts['excluded_pixels'] += int((m == 1).sum()-keep.sum())
        if keep.any():
            counts['min_pet'] = min(counts['min_pet'],float(p[keep].min()))
            counts['max_pet'] = max(counts['max_pet'],float(p[keep].max()))
    support = target = mask = fallback = wind = None
    counts['unresolved_land_pixels'] = counts['land_pixels_lgn']-counts['supported_pixels']
    (APP/'server/pet-version-1.partial.tif').replace(APP/'server/pet-version-1.tif')
    (ROOT/'boundary-mask.tif').unlink()
    if not counts['supported_pixels']:
        raise RuntimeError('No supported Walcheren PET pixels')
    counts['resolution_m'] = RESOLUTION
    counts['supported_area_km2'] = counts['supported_pixels']*RESOLUTION**2/1e6
    counts['land_area_km2'] = counts['land_pixels_lgn']*RESOLUTION**2/1e6
    return counts


def main():
    check_storage_budget(ROOT)
    gdal.UseExceptions()
    ogr.UseExceptions()
    gdal.SetCacheMax(256*1024**2)
    gdal.SetConfigOption('GDAL_PAM_ENABLED','NO')
    gdal.SetConfigOption('GDAL_NUM_THREADS','1')
    os.environ['CPL_TMPDIR'] = '/tmp'
    app = init_qgis()
    preparation = json.loads((PREP/'manifest.json').read_text())
    if set(preparation['stages']) != {'dsm','dtm','svf','classes'}:
        raise ValueError('Complete source preparation first')
    if preparation.get('pet_resolution_m', 1) != RESOLUTION:
        raise ValueError('Generation resolution must match source preparation')
    bounds = preparation['processing_extent_wsen']
    state_path = ROOT/'generation.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        'status':'running','bounds_wsen':bounds,'resolution_m':RESOLUTION,'weather':'Existing fixed PET weather/date defaults',
        'fallback_pedestrian_wind_m_s':FALLBACK,'scientific_validation':False,
        'gdal_version':gdal.VersionInfo(),'qgis_version':Qgis.QGIS_VERSION,
        'calculation_defaults': {'ff10':7.677,'t_min':27.2,'t_max':29.1,
            'average_wind_speed':7.5,'base_temperature':28.3,'relative_humidity':44,
            'phi':44,'q_gl':663,'q_diff':.2,'uhi_datetime':'2017-07-01T18:00:00',
            'shadow_datetime':'2015-07-01T15:00:00','latitude':51.498,'longitude':3.613},
        'runtime':{'gdal_cache_mib':256,'gdal_threads':1,'wind_direction':'west',
                   'wind_cell_m':[140,280],'wind_tile_m':[2800,2800],
                   'wind_context_cells':1,'wind_workers':int(os.environ.get('WIND_WORKERS','1')),
                   'fresh_wind_process':os.environ.get('FRESH_WIND_PROCESS')=='1','free_space_reserve_gib':10},
        'stages':{},'code_sha256':digest(Path(__file__)),
        'vector_selection': 'BAG excludes demolished, unrealised and incorrectly registered statuses to match the saved legacy WFS count; BGT point selection follows current service',
        'source_provenance': {str(p):digest(p) for p in [ROOT/'boundary/source.json',
            PREP/'manifest.json',Path('/data/zeeland/sources/lgn2018/source.json'),
            Path('/data/zeeland/sources/svf-nl-3/manifest.json'),
            Path('/data/zeeland/acquisition-20261005/elevation-dsm/manifest.json'),
            Path('/data/zeeland/acquisition-20261005/elevation-dtm/manifest.json')]}}
    if state['code_sha256'] != digest(Path(__file__)):
        raise ValueError('Generation script changed; review before resuming')
    state['status'] = 'running'
    state.pop('error',None)
    state.pop('failed_stage',None)
    save(state_path,state)
    for folder in ('raster','server/styles','uhi','pet','shadow-maps'):
        (APP/folder).mkdir(parents=True,exist_ok=True)
    virtual = preparation.get('elevation_storage') == 'virtual'
    published_dsm = APP/('dsm.vrt' if virtual else 'dsm.TIF')
    if not published_dsm.exists():
        if virtual:
            copy_virtual_raster(elevation_path(PREP,'dsm'), published_dsm)
        else:
            os.link(elevation_path(PREP,'dsm'), published_dsm)
    def stage(name, action):
        if name in state['stages']:
            return
        if shutil.disk_usage(ROOT).free < 10*1024**3:
            raise RuntimeError('Reached the 10 GiB free-space reserve')
        check_storage_budget(ROOT, round((bounds[2]-bounds[0])*(bounds[3]-bounds[1])/RESOLUTION**2)*8)
        started = time.perf_counter()
        try:
            action()
        except Exception as exc:
            state.update(status='failed',failed_stage=name,error=str(exc))
            save(state_path,state)
            raise
        state['stages'][name] = {'seconds':time.perf_counter()-started}
        save(state_path,state)
        print(f'Completed {name}: {state["stages"][name]}',flush=True)
    stage('thematic', lambda: prepare_thematic(bounds))
    stage('wind',lambda:wind_grid(bounds,state,prepare_vectors()))
    grid = APP/'server/wind-speed-calc.geojson'
    stage('zonal_pet', lambda: calculate_zones(grid,bounds))
    pet, raster = PETService(), RasterService()
    filled = APP/'raster/svf-reproject-filled.tif'
    stage('svf_fill',lambda:raster.fill_nodata_gdal(str(PREP/'svf.tif'),str(filled),distance=10/RESOLUTION,
                                                tiled_output=virtual))
    def tile_svf():
        # gdal_calc reads blocks from its first, tiled input. FillNoData's
        # full-width row strips amplify I/O when those tile windows are read.
        temporary = filled.with_name('svf-tiled.partial.tif')
        original = gdal.Open(str(filled))
        converted = gdal.Translate(str(temporary),original,creationOptions=OPTIONS)
        converted = None
        converted = gdal.Open(str(temporary))
        if (original.RasterXSize,original.RasterYSize,original.GetGeoTransform(),
            original.GetProjection(),original.GetRasterBand(1).GetNoDataValue()) != (
            converted.RasterXSize,converted.RasterYSize,converted.GetGeoTransform(),
            converted.GetProjection(),converted.GetRasterBand(1).GetNoDataValue()):
            raise RuntimeError('SVF tiling changed the raster grid or NoData')
        for y in range(0,original.RasterYSize,128):
            h = min(128,original.RasterYSize-y)
            if original.ReadRaster(0,y,original.RasterXSize,h) != converted.ReadRaster(
                    0,y,converted.RasterXSize,h):
                raise RuntimeError('Lossless SVF tiling changed pixel bytes')
        original = converted = None
        temporary.replace(filled)
    stage('svf_tiles',tile_svf)
    stage('sun',lambda:pet.calculate_total_pet_sun(str(APP/'uhi/sun-bbox.tif'),
        str(APP/'raster/br-reproject.tif'),str(filled),str(APP/'uhi/sun-pet.tif'),exact_grid=True))
    def shadow():
        temporary = APP/'uhi/shadow-unclipped.tif'
        pet.calculate_total_pet_shadow(str(APP/'uhi/shadow-bbox.tif'),str(filled),
                                       str(APP/'uhi/t_a.tif'),str(temporary),tiled_output=True)
        w,s,e,n = bounds
        gdal.Translate(str(APP/'uhi/shadow-pet.tif'),str(temporary),projWin=[w,n,e,s],creationOptions=OPTIONS)
        temporary.unlink()
    stage('shadow',shadow)
    discard_stage_temporaries('shadow',state)
    dt = datetime(2015,7,1,15)
    hillshade = APP/'shadow-maps/hillshade_20150701_1500.tif'
    stage('hillshade',lambda:ShadowService().generate_hillshade_maps(str(published_dsm),
        str(APP/'shadow-maps'),51.498,3.613,dt,dt,tiled_output=virtual))
    raw = APP/'pet/combined.tif'
    stage('combine',lambda:pet.calculate_total_pet_map(str(hillshade),str(APP/'uhi/sun-pet.tif'),
        str(APP/'uhi/shadow-pet.tif'),str(raw),workspace=str(APP/'shadow-maps'),exact_grid=True))
    discard_stage_temporaries('combine',state)
    if 'support_mask' not in state['stages']:
        state['coverage'] = mask_pet(bounds,raw)
        state['stages']['support_mask'] = {'complete':True}
        save(state_path,state)
    discard_stage_temporaries('support_mask',state)
    style = Path('/data/server/styles/new-default.qml')
    shutil.copyfile(style,APP/'server/styles/new-default.qml')
    project = QgsProject()
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    layer = QgsRasterLayer(str(APP/'server/pet-version-1.tif'),'pet-version-1')
    layer.loadNamedStyle(str(style))
    project.addMapLayer(layer)
    zones = APP/'server/pet-zones.gpkg'
    project.addMapLayer(diagnostic_wind(zones,'wind','geschaalde_u_1_2',[0,1,2,4,6,8,100]))
    project.addMapLayer(diagnostic_wind(zones,'wind-speed-calc','u_1_2',[0,.25,.5,1,2,4,100]))
    for path,name,stops in [
        (published_dsm,'R_65DN1',[(-2,'#2166ac'),(5,'#f7f7f7'),(30,'#b2182b')]),
        (filled,'SVF_r65dn1',[(0,'#2166ac'),(.5,'#ffffbf'),(1,'#b2182b')]),
        (APP/'raster/br-reproject.tif','bowen-ratio-middelburg',[(.4,'#1a9850'),(3,'#a6611a')]),
        (APP/'raster/vegetation-reproject.tif','vegetation-lgn',[(0,'#d9b38c'),(1,'#1a9850')])]:
        project.addMapLayer(diagnostic_raster(path,name,stops))
    tree_path = APP/'server/trees.gpkg'
    if not tree_path.exists():
        boundary = ogr.Open(str(BOUNDARY))
        feature = boundary.GetLayer().GetNextFeature()
        source = ogr.Open(str(ROOT/'trees.gpkg'))
        source.GetLayer().SetSpatialFilter(feature.GetGeometryRef())
        temporary = tree_path.with_name('trees.partial.gpkg')
        output = ogr.GetDriverByName('GPKG').CreateDataSource(str(temporary))
        output.CopyLayer(source.GetLayer(),'trees')
        output = source = boundary = None
        temporary.replace(tree_path)
    trees = QgsVectorLayer(str(tree_path),'trees','ogr')
    if not trees.isValid():
        raise RuntimeError('Invalid cached tree publication layer')
    project.addMapLayer(trees)
    project.writeEntry('WFSLayers','',[trees.id()])
    project.setCrs(layer.crs())
    project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(layer.extent(),layer.crs()))
    if not project.write(str(APP/'server/starting-map.qgz')):
        raise RuntimeError('Could not write Walcheren project')
    # The existing nginx starting-map WFS route resolves server/map.qgz.
    shutil.copyfile(APP/'server/starting-map.qgz',APP/'server/map.qgz')
    state['published_trees'] = trees.featureCount()
    state['status'] = 'complete'
    state['baseline_sha256'] = digest(APP/'server/pet-version-1.tif')
    state['process_peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    save(state_path,state)
    print(json.dumps(state['coverage']),flush=True)
    project.clear()
    layer = project = pet = raster = None
    gc.collect()
    app.exitQgis()
    app = None
    gc.collect()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--wind-window':
        gdal.UseExceptions()
        gdal.SetCacheMax(256*1024**2)
        worker_app = init_qgis()
        job = json.loads(Path(sys.argv[2]).read_text())
        calculate_wind_window(job['context'],job['vectors'],Path(job['destination']))
        QgsProject.instance().clear()
        gc.collect()
        worker_app.exitQgis()
    else:
        main()
