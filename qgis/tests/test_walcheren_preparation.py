"""Recorded reused SVF files must be included in the regional mosaic."""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_walcheren import svf_sources


@pytest.mark.parametrize('pet_resolution', [1, 2])
def test_native_elevation_budget_uses_half_metre_grid(tmp_path, monkeypatch, pet_resolution):
    """A budget fitting a 1 m band must reject its four-times-larger native DSM."""
    import zipfile
    from osgeo import gdal, ogr, osr
    import prepare_walcheren as preparation

    data = tmp_path/'data'
    root = data/'zeeland'
    out = tmp_path/'trial'
    boundary = out/'boundary'
    boundary.mkdir(parents=True)
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(28992)
    vector = ogr.GetDriverByName('GPKG').CreateDataSource(str(boundary/'region-rd.gpkg'))
    layer = vector.CreateLayer('region', crs, ogr.wkbPolygon)
    geometry = ogr.CreateGeometryFromWkt(
        'POLYGON ((30000 393190,30140 393190,30140 393470,30000 393470,30000 393190))')
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(geometry)
    assert layer.CreateFeature(feature) == 0
    vector = None
    (boundary/'source.json').write_text(json.dumps({'extent_wsen':[30000,393190,30140,393470]}))
    # The prepared extent includes the existing 140/280 m margins: 420 x 840 m.
    # A Float32 1 m band needs 1,411,200 bytes; its 0.5 m native band needs 5,644,800.
    (out/'storage-budget.json').write_text(json.dumps({
        'max_dataset_bytes':3_000_000, 'minimum_free_bytes':0}))
    for name in ('dsm', 'dtm'):
        folder = root/f'acquisition-20261005/elevation-{name}'
        folder.mkdir(parents=True)
        ds = gdal.GetDriverByName('GTiff').Create(str(folder/'source.tif'),840,1680,1,
            gdal.GDT_Float32,options=['TILED=YES','COMPRESS=DEFLATE'])
        ds.SetGeoTransform((29860,.5,0,393750,0,-.5))
        ds.SetProjection(crs.ExportToWkt())
        ds.GetRasterBand(1).Fill(1)
        ds = None
        vrt = gdal.BuildVRT(str(folder/'native.vrt'),[str(folder/'source.tif')])
        vrt = None
    lgn = root/'sources/lgn2018'
    lgn.mkdir(parents=True)
    classes = lgn/'source.tif'
    ds = gdal.GetDriverByName('GTiff').Create(str(classes),84,168,1,gdal.GDT_Int16)
    ds.SetGeoTransform((29860,5,0,393750,0,-5))
    ds.SetProjection(crs.ExportToWkt())
    ds.GetRasterBand(1).Fill(1)
    ds = None
    with zipfile.ZipFile(lgn/'LGN2018.zip','w') as archive:
        archive.write(classes,'LGN2018/LGN2018.tif')
    svf = root/'sources/svf-nl-3'
    svf.mkdir(parents=True)
    source = svf/'source.tif'
    ds = gdal.GetDriverByName('GTiff').Create(str(source),420,840,1,gdal.GDT_Float32,
        options=['TILED=YES','COMPRESS=DEFLATE'])
    ds.SetGeoTransform((29860,1,0,393750,0,-1))
    ds.SetProjection(crs.ExportToWkt())
    ds.GetRasterBand(1).Fill(.5)
    ds = None
    (svf/'manifest.json').write_text(json.dumps({'files':[{
        'local_path':str(source), 'bytes':source.stat().st_size}]}))
    monkeypatch.setattr(preparation,'ROOT',root)
    monkeypatch.setattr(preparation,'OUT',out)
    monkeypatch.setattr(preparation,'RESOLUTION',pet_resolution)
    if pet_resolution == 1:
        with pytest.raises(ValueError,match='Dataset storage budget reached'):
            preparation.main()
        assert not (out/'prepared/dsm.tif').exists()
    else:
        monkeypatch.setattr(preparation,'ELEVATION_STORAGE','virtual')
        preparation.main()
        for name, resolution in [('dsm.vrt',1),('dtm.vrt',1),('svf.tif',2),
                                 ('classes.tif',2),('region-mask.tif',2)]:
            ds = gdal.Open(str(out/'prepared'/name))
            assert ds.GetGeoTransform() == (29860,resolution,0,393750,0,-resolution)
            assert (ds.RasterXSize,ds.RasterYSize) == (420/resolution,840/resolution)
            assert ds.GetRasterBand(1).ReadAsArray(100,100,1,1)[0,0] != -9999
            ds = None


def manifest(tmp_path):
    root=tmp_path/'data'
    folder=root/'zeeland/sources/svf-nl-3'
    folder.mkdir(parents=True)
    paths=[folder/'new.tif',root/'zeeland/acquisition-20261005/svf/reused.tif']
    for path in paths:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'source')
    entries=[{'local_path':str(Path('data')/p.relative_to(root)),'bytes':6} for p in paths]
    (folder/'manifest.json').write_text(json.dumps({'files':entries}))
    return root,paths


def test_reused_tiles_outside_download_folder_are_included(tmp_path):
    root,paths=manifest(tmp_path)
    assert svf_sources(root)==list(map(str,paths))


def test_missing_recorded_tile_fails_instead_of_publishing_a_gap(tmp_path):
    root,paths=manifest(tmp_path)
    paths[1].unlink()
    with pytest.raises(ValueError,match='Missing or changed recorded SVF source'):
        svf_sources(root)
