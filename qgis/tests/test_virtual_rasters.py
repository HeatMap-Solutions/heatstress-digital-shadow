"""Native virtual inputs and lossless storage must preserve actual GIS pixels."""
from datetime import datetime
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest
from osgeo import gdal

from test_local_pet import raster, read
from src.services.shadow_service import ShadowService
from src.services.raster_service import RasterService
from src.utils.raster_sources import copy_virtual_raster, elevation_path
from src.utils.storage_budget import check_storage_budget


def test_virtual_native_crop_and_relocation_preserve_pixels(tmp_path):
    source = tmp_path/'native.tif'
    values = np.arange(10000,dtype='float32').reshape(100,100)/7
    values[20:25,40:50] = -9999
    raster(source,values,resolution=.5)
    archive = tmp_path/'native.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as output:
        output.write(source,'native.tif')
    prepared = tmp_path/'prepared'
    prepared.mkdir()
    kwargs = dict(outputBounds=[30010,393710,30040,393740],xRes=.5,yRes=.5,
        resampleAlg='near',dstNodata=-9999,outputType=gdal.GDT_Float32)
    src = f'/vsizip/{archive}/native.tif'
    real = gdal.Warp(str(prepared/'dsm.tif'),src,**kwargs)
    virtual = gdal.Warp(str(prepared/'dsm.vrt'),src,format='VRT',**kwargs)
    np.testing.assert_array_equal(real.ReadAsArray(),virtual.ReadAsArray())
    assert real.GetGeoTransform() == virtual.GetGeoTransform()
    assert real.GetProjection() == virtual.GetProjection()
    real = virtual = None
    serving = tmp_path/'serving'
    serving.mkdir()
    copy_virtual_raster(prepared/'dsm.vrt',serving/'dsm.vrt')
    np.testing.assert_array_equal(read(prepared/'dsm.tif'),read(serving/'dsm.vrt'))
    assert elevation_path(prepared,'dsm') == prepared/'dsm.vrt'
    assert (prepared/'dsm.vrt').stat().st_size < 10000


def test_compressed_hillshade_and_fill_are_lossless(tmp_path,qgis_app):
    source = tmp_path/'surface.tif'
    y,x = np.indices((80,80))
    values = (x/3+y/7).astype('float32')
    values[30:33,30:33] = -9999
    raster(source,values)
    plain,compact = tmp_path/'plain',tmp_path/'compact'
    plain.mkdir();compact.mkdir()
    date = datetime(2015,7,1,15)
    service = ShadowService()
    a = service.generate_hillshade_maps(str(source),str(plain),51.498,3.613,date,date)
    b = service.generate_hillshade_maps(str(source),str(compact),51.498,3.613,date,date,tiled_output=True)
    np.testing.assert_array_equal(read(a),read(b))
    fill = RasterService()
    fill.fill_nodata_gdal(str(source),str(plain/'filled.tif'))
    fill.fill_nodata_gdal(str(source),str(compact/'filled.tif'),tiled_output=True)
    np.testing.assert_array_equal(read(plain/'filled.tif'),read(compact/'filled.tif'))


def test_budget_includes_external_virtual_dependencies(tmp_path):
    source = tmp_path/'archive.zip'
    source.write_bytes(b'input'*100)
    root = tmp_path/'app'
    root.mkdir()
    (root/'storage-budget.json').write_text(json.dumps({
        'max_dataset_bytes':400,'minimum_free_bytes':0,
        'dependency_paths':[str(source),str(source)]}))
    with pytest.raises(ValueError,match='storage budget reached'):
        check_storage_budget(root)
    source.unlink()
    with pytest.raises(FileNotFoundError):
        check_storage_budget(root)
