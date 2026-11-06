"""Exercise shadow generation with the installed QGIS algorithms and a real raster."""

from datetime import datetime

import numpy as np
from osgeo import gdal, osr

from src.services.shadow_service import ShadowService


def test_shadow_maps_preserve_grid_and_respond_to_sun_time(tmp_path, qgis_app):
    source = str(tmp_path / "flat.tif")
    driver = gdal.GetDriverByName("GTiff")
    raster = driver.Create(source, 20, 20, 1, gdal.GDT_Float32)
    transform = (31000, 1, 0, 392000, 0, -1)
    raster.SetGeoTransform(transform)
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(28992)
    raster.SetProjection(crs.ExportToWkt())
    raster.GetRasterBand(1).WriteArray(np.zeros((20, 20), dtype=np.float32))
    raster = None

    result = ShadowService().generate_hillshade_maps(
        source,
        str(tmp_path),
        51.498,
        3.613,
        datetime(2015, 7, 1, 9),
        datetime(2015, 7, 1, 12),
    )
    assert result.endswith("hillshade_20150701_1200.tif")

    values = []
    for hour in range(9, 13):
        output = gdal.Open(str(tmp_path / f"hillshade_20150701_{hour:02d}00.tif"))
        assert output is not None
        assert (output.RasterXSize, output.RasterYSize) == (20, 20)
        assert output.GetGeoTransform() == transform
        actual_crs = osr.SpatialReference(wkt=output.GetProjection())
        assert actual_crs.IsSame(crs)
        interior = output.GetRasterBand(1).ReadAsArray()[1:-1, 1:-1]
        assert np.all(interior > 0)
        assert np.unique(interior).size == 1
        values.append(int(interior[0, 0]))

    assert values[-1] > values[0]
