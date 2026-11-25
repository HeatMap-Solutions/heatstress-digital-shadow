"""Tests for the comparison with a reference map (src/services/reference_comparison.py).

Offline: a small synthetic map and a fake reference service, no internet needed.

    docker exec -w /app qgis python3 -m pytest tests/test_reference_comparison.py -v
"""
import numpy as np
from osgeo import gdal, osr
import pytest

from src.services.reference_comparison import compare, rows, sample_points, summarise

gdal.UseExceptions()


def raster(path, values, left=30000, top=393750):
    ds = gdal.GetDriverByName('GTiff').Create(str(path), values.shape[1], values.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((left, 1, 0, top, 0, -1))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(28992)
    ds.SetProjection(crs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(-9999)
    ds.GetRasterBand(1).WriteArray(values.astype('float32'))
    ds = None
    return path


@pytest.fixture
def maps(tmp_path):
    """PET 30 °C (sun) on the left half, 25 °C in the first 10 columns (shade), NoData (water) on the right half."""
    pet = np.full((100, 100), -9999.0)
    pet[:, :50] = 30.0
    pet[:, :10] = 25.0                       # these pixels use the shade formula
    shadow = np.full((100, 100), 25.0)
    svf = np.full((100, 100), 0.7)
    return (raster(tmp_path/'pet.tif', pet), raster(tmp_path/'shadow.tif', shadow), raster(tmp_path/'svf.tif', svf))


def test_points_only_where_our_map_has_a_value(maps):
    pet, shadow, svf = maps
    points = sample_points(pet, 50, seed=1, shadow_path=shadow, svf_path=svf)
    assert len(points) == 50
    assert all(30000 <= p.x < 30050 for p in points)            # never in the NoData half
    assert all(p.ours in (25.0, 30.0) for p in points)
    assert all((p.ours == 25.0) == p.in_shade for p in points)   # shade = PET equals the shade PET
    assert all(p.svf == pytest.approx(0.7) for p in points)


def test_same_seed_gives_the_same_points(maps):
    pet = maps[0]
    first = [(p.x, p.y) for p in sample_points(pet, 20, seed=42)]
    assert first == [(p.x, p.y) for p in sample_points(pet, 20, seed=42)]
    assert first != [(p.x, p.y) for p in sample_points(pet, 20, seed=43)]


def test_points_are_pixel_centres_with_wgs84(maps):
    point = sample_points(maps[0], 1, seed=3)[0]
    assert point.x % 1 == 0.5 and point.y % 1 == 0.5
    assert 3.5 < point.lon < 3.7 and 51.4 < point.lat < 51.6    # Middelburg, lon before lat


def test_summary_known_values():
    s = summarise([30, 32, 34, None], [29, 32, 36, 31])
    assert s['compared'] == 3                                   # the None is skipped
    assert s['mean_difference'] == pytest.approx((1+0-2)/3, abs=0.01)
    assert s['mean_abs_difference'] == 1.0
    assert s['rmse'] == pytest.approx(np.sqrt(5/3), abs=0.01)
    assert s['within_1C_pct'] == pytest.approx(66.7)
    assert s['within_2C_pct'] == 100.0


def test_summary_without_pairs():
    assert summarise([None, 30], [31, None]) == {'compared': 0}


def test_compare_splits_sun_and_shade(maps):
    pet, shadow, _ = maps
    points = sample_points(pet, 40, seed=5, shadow_path=shadow)
    fake = lambda service, layer, x, y: 28.0 if x < 30040 else None    # reference has no value at some points
    values, summary = compare(points, ['test:layer'], 'http://fake/{workspace}/wms', fetch=fake)
    result = summary['test:layer']
    assert result['reference_no_value'] == sum(p.x >= 30040 for p in points)
    assert result['sun']['compared'] + result['shade']['compared'] == result['all']['compared']
    if result['shade']['compared']:
        assert result['shade']['mean_difference'] == pytest.approx(25.0-28.0)
    table = list(rows(points, values))
    assert len(table) == 40 and 'diff test:layer' in table[0]