"""Test fallback units and exclusion of missing zonal inputs with real QGIS."""
import json
from pathlib import Path
import sys

import numpy as np
from osgeo import gdal, ogr, osr

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import generate_walcheren as generation
from src.services.pet_service import PETService
from test_local_pet import write_grid, raster, read


def test_fallback_is_pedestrian_wind_after_scaling(tmp_path, monkeypatch, qgis_app):
    app, prep = tmp_path/'app', tmp_path/'prep'
    (app/'server').mkdir(parents=True)
    (app/'uhi').mkdir()
    prep.mkdir()
    monkeypatch.setattr(generation,'APP',app)
    monkeypatch.setattr(generation,'PREP',prep)
    grid = app/'server/grid.geojson'
    write_grid(grid,cols=3,rows=1)
    data = json.loads(grid.read_text())
    data['crs']['type'] = 'name'
    for feature in data['features']:
        col = feature['properties']['col_index']
        feature['properties'].update(u_1_2=.5 if col == 1 else 0,wind_fallback=0 if col == 1 else 1)
    grid.write_text(json.dumps(data))
    raster(app/'raster/vegetation-reproject.tif',np.ones((80,120),dtype='float32'))
    values = np.full((80,120),.6,dtype='float32')
    values[:,80:] = -9999
    raster(prep/'svf.tif',values)
    generation.calculate_zones(grid,[30000,393670,30120,393750])
    ds = ogr.Open(str(app/'server/pet-zones.gpkg'))
    layer = ds.GetLayer()
    assert layer.GetFeatureCount() == 2
    winds = {f['col_index']:f['geschaalde_u_1_2'] for f in layer}
    assert winds[0] == 7.5
    # Established scaler result for raw u_1_2=0.5 and existing ff10=7.677.
    assert winds[1] == 3.923845209
    result = read(app/'uhi/sun-bbox.tif')
    assert np.all(np.isfinite(result[:,:80]))
    assert np.all(result[:,:80] != -9999)
    assert np.all(result[:,80:] == -9999)


def test_output_mask_distinguishes_water_source_gaps_and_fallback(tmp_path,monkeypatch):
    app,prep = tmp_path/'app',tmp_path/'prep'
    (app/'server').mkdir(parents=True)
    (app/'raster').mkdir()
    prep.mkdir()
    (tmp_path/'boundary').mkdir()
    monkeypatch.setattr(generation,'APP',app)
    monkeypatch.setattr(generation,'PREP',prep)
    monkeypatch.setattr(generation,'ROOT',tmp_path)
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(28992)
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(tmp_path/'boundary/walcheren-rd.gpkg'))
    layer = ds.CreateLayer('boundary',srs=crs,geom_type=ogr.wkbPolygon)
    f = ogr.Feature(layer.GetLayerDefn())
    f.SetGeometry(ogr.CreateGeometryFromWkt('POLYGON((30000 393750,30080 393750,30080 393670,30000 393670,30000 393750))'))
    layer.CreateFeature(f)
    layer = f = ds = None
    classes = np.full((80,80),18,dtype='float32')
    classes[:,:10] = 16
    raster(prep/'classes.tif',classes)
    dsm = np.ones((80,80),dtype='float32')
    dsm[:,20:30] = -9999
    raster(prep/'dsm.tif',dsm)
    svf = np.full((80,80),.6,dtype='float32')
    svf[:,30:40] = -9999
    raster(app/'raster/svf-reproject-filled.tif',svf)
    raw = tmp_path/'raw.tif'
    raster(raw,np.full((80,80),30,dtype='float32'))
    grid = app/'server/wind-speed-calc.geojson'
    write_grid(grid,cols=2,rows=1)
    data = json.loads(grid.read_text())
    data['crs']['type'] = 'name'
    for f in data['features']:
        f['properties']['wind_fallback'] = int(f['properties']['col_index'] == 0)
    grid.write_text(json.dumps(data))
    counts = generation.mask_pet([30000,393670,30080,393750],raw)
    assert counts['land_pixels_lgn'] == 5600
    assert counts['supported_pixels'] == 4000
    assert counts['unresolved_land_pixels'] == 1600
    assert counts['supported_fallback_pixels'] == 800
    actual = read(app/'server/pet-version-1.tif')
    assert np.all(actual[:,:10] == -9999)
    assert np.all(actual[:,20:40] == -9999)
    assert np.all(actual[:,10:20] == 30)
    assert np.all(actual[:,40:] == 30)


def test_exact_grid_calculations_keep_consistent_tiled_blocks(tmp_path,qgis_app):
    paths = {name:tmp_path/(name+'.tif') for name in ('partial','bowen','svf','hillshade','shade')}
    for name,value in [('partial',30),('bowen',.4),('svf',.6),('hillshade',200),('shade',24)]:
        raster(paths[name],np.full((20,20),value,dtype='float32'))
    sun,combined = tmp_path/'sun.tif',tmp_path/'combined.tif'
    service = PETService()
    service.calculate_total_pet_sun(str(paths['partial']),str(paths['bowen']),
                                    str(paths['svf']),str(sun),exact_grid=True)
    service.calculate_total_pet_map(str(paths['hillshade']),str(sun),str(paths['shade']),
                                    str(combined),workspace=str(tmp_path),exact_grid=True)
    for path in (sun,combined,tmp_path/'shadow_map_aligned.tif'):
        ds = gdal.Open(str(path))
        assert ds.GetRasterBand(1).GetBlockSize() == [256,256]
    np.testing.assert_array_equal(read(combined),read(sun))


def test_tiled_shadow_preserves_legacy_values_and_grid(tmp_path,qgis_app):
    paths = {name:tmp_path/(name+'.tif') for name in ('partial','svf','temperature')}
    for name,value in [('partial',24),('svf',.6),('temperature',28.3)]:
        raster(paths[name],np.full((20,20),value,dtype='float32'))
    service = PETService()
    outputs = [tmp_path/'legacy.tif',tmp_path/'tiled.tif']
    for tiled,path in zip((False,True),outputs):
        service.calculate_total_pet_shadow(str(paths['partial']),str(paths['svf']),
                                           str(paths['temperature']),str(path),tiled_output=tiled)
    legacy,tiled = [gdal.Open(str(path)) for path in outputs]
    assert tiled.GetRasterBand(1).GetBlockSize() == [256,256]
    assert (legacy.RasterXSize,legacy.RasterYSize,legacy.GetGeoTransform(),legacy.GetProjection(),
            legacy.GetRasterBand(1).GetNoDataValue()) == (
            tiled.RasterXSize,tiled.RasterYSize,tiled.GetGeoTransform(),tiled.GetProjection(),
            tiled.GetRasterBand(1).GetNoDataValue())
    np.testing.assert_array_equal(legacy.ReadAsArray(),tiled.ReadAsArray())


def test_parallel_wind_matches_serial_and_reuses_checked_windows(tmp_path,monkeypatch):
    from threading import Barrier
    reference = tmp_path/'reference.geojson'
    features = []
    for col in range(40):
        left,right = 30000+col*140,30000+(col+1)*140
        features.append({'type':'Feature','properties':{
            'lambda_total':.1,'u_h':1.,'u_1_2':.5 if col < 20 else .25},
            'geometry':{'type':'Polygon','coordinates':[[[left,393750],
                [right,393750],[right,393470],[left,393470],[left,393750]]]}})
    reference.write_text(json.dumps({'type':'FeatureCollection','features':features,
        'crs':{'type':'name','properties':{'name':'urn:ogc:def:crs:EPSG::28992'}}}))
    barrier = Barrier(2)
    def window(context,vectors,destination):
        if __import__('os').environ['WIND_WORKERS'] == '2':
            barrier.wait(timeout=10)
        # Independent datasets stand in for already-proved isolated GIS workers.
        result = gdal.VectorTranslate(str(destination),str(reference),format='GPKG',spatFilter=context)
        assert result is not None
        result = None
    monkeypatch.setattr(generation,'isolated_wind_window',window)
    monkeypatch.setenv('FRESH_WIND_PROCESS','1')
    outputs = []
    for workers in (1,2):
        root = tmp_path/str(workers)
        (root/'app/server').mkdir(parents=True)
        raster(root/'prep/classes.tif',np.full((280,5600),18,dtype='float32'))
        monkeypatch.setattr(generation,'ROOT',root)
        monkeypatch.setattr(generation,'PREP',root/'prep')
        monkeypatch.setattr(generation,'APP',root/'app')
        monkeypatch.setenv('WIND_WORKERS',str(workers))
        state = {}
        output = generation.wind_grid([30000,393470,35600,393750],state,{})
        outputs.append(json.loads(output.read_text()))
        assert set(state['wind_tiles']) == {'0-0','20-0'}
    assert outputs[0] == outputs[1]
    assert len(outputs[1]['features']) == 40
    assert state['wind_cells']['fallback'] == 0
    def unexpected_worker(*args):
        raise AssertionError('Resume must reuse successful windows')
    monkeypatch.setattr(generation,'isolated_wind_window',unexpected_worker)
    assert json.loads(generation.wind_grid([30000,393470,35600,393750],state,{}).read_text()) == outputs[1]
    Path(state['wind_tiles']['0-0']['path']).write_bytes(b'changed checkpoint')
    import pytest
    with pytest.raises(RuntimeError,match='Changed wind checkpoint'):
        generation.wind_grid([30000,393470,35600,393750],state,{})


def test_parallel_wind_rejects_shared_qgis_process(tmp_path,monkeypatch):
    import pytest
    (tmp_path/'prep').mkdir()
    raster(tmp_path/'prep/classes.tif',np.ones((280,140),dtype='float32'))
    monkeypatch.setattr(generation,'ROOT',tmp_path)
    monkeypatch.setattr(generation,'PREP',tmp_path/'prep')
    monkeypatch.setenv('WIND_WORKERS','2')
    monkeypatch.delenv('FRESH_WIND_PROCESS',raising=False)
    with pytest.raises(ValueError,match='requires isolated GIS processes'):
        generation.wind_grid([30000,393470,30140,393750],{}, {})
