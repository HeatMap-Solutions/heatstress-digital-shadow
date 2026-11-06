"""Coarse preparation must stay coarse through height, PET and scenario stages."""
import json
from pathlib import Path
import shutil
import sys
import uuid
import zipfile

import numpy as np
import pytest
from osgeo import gdal, ogr, osr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import generate_walcheren as generation
import prepare_walcheren as preparation
from src.services.local_pet_service import LocalPETService, RasterLayout
from src.services.wind.height_service import HeightService
from src.services.wind.grid_service import GridService
from qgis.core import QgsExpression, QgsExpressionContext, QgsFeature, QgsFields, QgsField
from qgis.PyQt.QtCore import QVariant
from src.utils.storage_budget import check_storage_budget
from test_local_pet import raster, read, point, prepared, assert_matches_full


def test_height_pipeline_does_not_expand_five_metre_data(tmp_path, qgis_app):
    dsm, dtm = tmp_path/'dsm.tif', tmp_path/'dtm.tif'
    raster(dsm, np.full((16,16), 12, dtype='float32'), resolution=5)
    raster(dtm, np.full((16,16), 2, dtype='float32'), resolution=5)
    service = HeightService()
    # Reproduce the old default expanding a coarse source back to 1 m.
    legacy = tmp_path/'legacy.tif'
    service.create_height_map(str(dsm), str(dtm), str(legacy))
    assert gdal.Open(str(legacy)).RasterXSize == 80
    coarse = tmp_path/'coarse.tif'
    service.create_height_map(str(dsm), str(dtm), str(coarse), raster_resolution=5)
    assert RasterLayout.read(coarse).transform[1:6:4] == (5,-5)
    np.testing.assert_array_equal(read(coarse), np.full((16,16),10,dtype='float32'))


def test_coarse_polygon_mask_and_area_units(tmp_path, monkeypatch):
    app, prep = tmp_path/'app-data', tmp_path/'prepared'
    (app/'server').mkdir(parents=True)
    (app/'raster').mkdir()
    prep.mkdir()
    boundary = tmp_path/'boundary/region-rd.gpkg'
    boundary.parent.mkdir()
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(28992)
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(boundary))
    layer = ds.CreateLayer('region', srs=crs, geom_type=ogr.wkbPolygon)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(ogr.CreateGeometryFromWkt('POLYGON((30000 393750,30060 393750,30060 393670,30000 393670,30000 393750))'))
    layer.CreateFeature(feature)
    feature = layer = ds = None
    monkeypatch.setattr(generation, 'ROOT', tmp_path)
    monkeypatch.setattr(generation, 'APP', app)
    monkeypatch.setattr(generation, 'PREP', prep)
    monkeypatch.setattr(generation, 'RESOLUTION', 5)
    classes = np.full((16,16),18,dtype='float32')
    classes[:,:2] = 16
    raster(prep/'classes.tif',classes,resolution=5)
    for name in [prep/'dsm.tif', app/'raster/svf-reproject-filled.tif']:
        raster(name,np.full((16,16),.6,dtype='float32'),resolution=5)
    raw = tmp_path/'raw.tif'
    raster(raw,np.full((16,16),30,dtype='float32'),resolution=5)
    (app/'server/wind-speed-calc.geojson').write_text(json.dumps({'type':'FeatureCollection','features':[]}))
    counts = generation.mask_pet([30000,393670,30080,393750],raw)
    assert counts['supported_pixels'] == 160
    assert counts['supported_area_km2'] == .004
    values = read(app/'server/pet-version-1.tif')
    assert values.shape == (16,16)
    assert np.all(values[:,:2] == -9999)
    assert np.all(values[:,12:] == -9999)
    assert np.all(values[:,2:12] == 30)


@pytest.mark.parametrize('resolution', [2, 5])
def test_five_metre_add_delete_matches_full_and_keeps_other_cells(prepared, tmp_path, resolution):
    root = tmp_path/'coarse'
    shutil.copytree(prepared, root)
    service = LocalPETService(root)
    for path in service.inputs.values():
        temporary = path.with_suffix('.coarse.tif')
        target_resolution = 1 if resolution == 2 and path == service.inputs['dsm'] else resolution
        ds = gdal.Warp(str(temporary), str(path), xRes=target_resolution, yRes=target_resolution,
                       resampleAlg='average', dstNodata=-9999)
        ds = None
        temporary.replace(path)
    baseline, _ = service.calculate_region(service.layouts()[1], {}, root/'coarse-baseline')
    shutil.copyfile(baseline, root/'server/pet-version-1.tif')
    sid = str(uuid.uuid4())
    folder = root/'server/sessions'/sid
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder/'map.qgz', 'w') as archive:
        archive.writestr('map.qgs', f'<qgis><projectlayers><maplayer><id>base</id><layername>pet-version-1</layername><datasource>{root}/server/pet-version-1.tif</datasource></maplayer></projectlayers></qgis>')
    added = service.update([point()], sid)
    assert len(added['updated_cells']) == 9
    assert_matches_full(service, added, [point()], tmp_path/'added', root/'server/pet-version-1.tif')
    deleted = service.update([], sid)
    np.testing.assert_array_equal(read(deleted['output']), read(root/'server/pet-version-1.tif'))


def test_storage_limit_rejects_before_creating_more_data(tmp_path):
    (tmp_path/'existing.tif').write_bytes(b'x'*100)
    limits = {'max_dataset_bytes':1000,'minimum_free_bytes':0}
    (tmp_path/'storage-budget.json').write_text(json.dumps(limits))
    check_storage_budget(tmp_path, 100)
    with pytest.raises(ValueError, match='storage budget reached'):
        check_storage_budget(tmp_path, 1000)


def test_storage_cleanup_keeps_scenario_inputs_and_requires_checkpoint(tmp_path, monkeypatch):
    app = tmp_path/'app-data'
    (app/'uhi').mkdir(parents=True)
    monkeypatch.setattr(generation, 'ROOT', tmp_path)
    monkeypatch.setattr(generation, 'APP', app)
    for name in ('shadow-bbox.tif', 't_a.tif', 'sun-bbox.tif', 'shadow-pet.tif'):
        (app/'uhi'/name).write_bytes(b'keep until consumed')
    state = {'stages': {}}
    monkeypatch.setenv('KEEP_INTERMEDIATE_MAPS', '0')
    with pytest.raises(ValueError, match='before the consuming stage completed'):
        generation.discard_stage_temporaries('shadow', state)
    assert (app/'uhi/shadow-bbox.tif').exists()
    state['stages']['shadow'] = {'complete': True}
    generation.discard_stage_temporaries('shadow', state)
    assert not (app/'uhi/shadow-bbox.tif').exists()
    assert not (app/'uhi/t_a.tif').exists()
    assert (app/'uhi/sun-bbox.tif').exists()
    assert (app/'uhi/shadow-pet.tif').exists()


def test_sequential_archive_reduction_matches_direct_average(tmp_path, monkeypatch):
    source = tmp_path/'native.tif'
    values = np.arange(10000,dtype='float32').reshape(100,100)
    values[:10,:10] = -9999
    raster(source,values,resolution=.5)
    archive = tmp_path/'source.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.write(source,'native.tif')
    folder = tmp_path/'acquisition-20261005/elevation-dsm'
    folder.mkdir(parents=True)
    second = tmp_path/'adjacent.tif'
    raster(second,np.where(values == -9999,-9999,values+100),resolution=.5)
    ds = gdal.Open(str(second),gdal.GA_Update)
    ds.SetGeoTransform((30050,.5,0,393750,0,-.5))
    ds = None
    second_archive = tmp_path/'adjacent.zip'
    with zipfile.ZipFile(second_archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.write(second,'adjacent.tif')
    entries = [
        {'archive':str(archive),'member':'native.tif','transform':[30000,.5,0,393750,0,-.5],'shape':[100,100],'expanded_bytes':source.stat().st_size},
        {'archive':str(second_archive),'member':'adjacent.tif','transform':[30050,.5,0,393750,0,-.5],'shape':[100,100],'expanded_bytes':second.stat().st_size},
    ]
    (folder/'manifest.json').write_text(json.dumps({'sources':entries}))
    prepared = tmp_path/'prepared'
    prepared.mkdir()
    monkeypatch.setattr(preparation,'ROOT',tmp_path)
    monkeypatch.setattr(preparation,'OUT',tmp_path)
    monkeypatch.setattr(preparation,'RESOLUTION',5)
    output = preparation.coarse_elevation('dsm',[30000,393700,30100,393750],prepared)
    reference = tmp_path/'reference.tif'
    mosaic = gdal.BuildVRT('',[str(source),str(second)])
    ds = gdal.Warp(str(reference),mosaic,xRes=5,yRes=5,resampleAlg='average',dstNodata=-9999)
    ds = None
    np.testing.assert_array_equal(read(output),read(reference))
    assert not list(prepared.glob('source-*'))


def test_budget_rejection_preserves_published_session(prepared, tmp_path):
    root = tmp_path/'bounded'
    shutil.copytree(prepared,root)
    (root/'storage-budget.json').write_text(json.dumps({'max_dataset_bytes':1,'minimum_free_bytes':0}))
    sid = str(uuid.uuid4())
    session = root/'server/sessions'/sid
    session.mkdir(parents=True)
    with zipfile.ZipFile(session/'map.qgz','w') as archive:
        archive.writestr('map.qgs',f'<qgis><projectlayers><maplayer><id>base</id><layername>pet-version-1</layername><datasource>{root}/server/pet-version-1.tif</datasource></maplayer></projectlayers></qgis>')
    before = (session/'map.qgz').read_bytes()
    with pytest.raises(ValueError,match='storage budget reached'):
        LocalPETService(root).update([point()],sid)
    assert (session/'map.qgz').read_bytes() == before
    assert not list(session.glob('pet_*_filled.tif'))


def test_frontal_area_uses_square_metres_at_both_resolutions(tmp_path, qgis_app):
    widths=[]
    for resolution, count in [(1,100),(5,4)]:
        path=tmp_path/f'aspect-{resolution}.tif'
        raster(path,np.ones((10//resolution,10//resolution),dtype='float32'),resolution=resolution)
        fields=QgsFields()
        fields.append(QgsField('pixels',QVariant.Double))
        fields.append(QgsField('objects',QVariant.Double))
        feature=QgsFeature(fields)
        feature.setAttributes([count,4])
        context=QgsExpressionContext()
        context.setFeature(feature)
        expression=QgsExpression(GridService.frontal_side_formula('pixels','objects',path))
        widths.append(expression.evaluate(context))
        assert not expression.hasEvalError()
    assert widths == [20,20]
    assert GridService.frontal_side_formula('pixels','objects',tmp_path/'aspect-1.tif') == 'sqrt("pixels" * "objects")'
