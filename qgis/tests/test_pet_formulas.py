"""Unit tests for the PET model (src/services/pet_formulas.py).

The expected values are worked out by hand from the formulas of the Standaard
Stresstest Hitte, or come from a published source (Stull 2011 for the wet bulb).
No data files are needed:

    docker exec -w /app qgis python3 -m pytest tests/test_pet_formulas.py -v
"""
import math

import numpy as np
import pytest
from osgeo import gdal, osr

gdal.UseExceptions()

from src.services import pet_formulas as pf
from src.services.scenario_grid import Bounds


# ---------- wind ----------

def test_wind_speed_from_the_wind_factor():
    # 7.677 * ((0.0796 - 0.0796) * 0.9175 + 0.1254) = 0.9627 m/s
    assert pf.wind_speed_1_2(np.array(0.0796)) == pytest.approx(7.677*0.1254)
    # 7.677 * ((1.0796 - 0.0796) * 0.9175 + 0.1254) = 8.0063 m/s
    assert pf.wind_speed_1_2(np.array(1.0796)) == pytest.approx(7.677*(0.9175+0.1254))


def test_wind_speed_below_half_a_metre_is_mirrored_as_in_main():
    # u = 0 gives 7.677 * (0.1254 - 0.0796*0.9175) = 0.4020 m/s, below 0.5, so 7.677 - 0.4020.
    low = 7.677*(0.1254-0.0796*0.9175)
    assert low < 0.5
    assert pf.wind_speed_1_2(np.array(0.0)) == pytest.approx(7.677-low)


# ---------- per zone cell ----------

def test_air_temperature_is_the_base_temperature_without_urban_heat_island():
    # SVF 1 and vegetation 1: (2 - 1 - 1) = 0, so no UHI.
    assert pf.air_temperature(1.0, 1.0) == pytest.approx(pf.BASE_TEMPERATURE)


def test_air_temperature_known_value():
    # base = (663 * (29.1-27.2)^3 / 7.5)^0.25 = 4.9623; UHI factor on 1 July 18:00 (table 5, '3/20') = 0.556
    base = (663*1.9**3/7.5)**0.25
    expected = 28.3 + (2-0.5-0.5)*base*0.556
    assert pf.air_temperature(0.5, 0.5) == pytest.approx(expected)
    assert expected == pytest.approx(31.059, abs=1e-3)


def test_more_vegetation_and_open_sky_give_cooler_air():
    assert pf.air_temperature(0.5, 0.8) < pf.air_temperature(0.5, 0.2)
    assert pf.air_temperature(0.9, 0.5) < pf.air_temperature(0.3, 0.5)


def test_wet_bulb_matches_stull_2011(monkeypatch):
    # Stull (2011), J. Appl. Meteor. Climatol. 50: T = 20 °C and RH = 50 % give Tw = 13.7 °C.
    monkeypatch.setattr(pf, 'RELATIVE_HUMIDITY', 50.0)
    assert pf.wet_bulb_temperature(np.array(20.0)) == pytest.approx(13.7, abs=0.05)


def test_partial_pet_shadow_known_value():
    # u = 1 m/s, so ln(u) = 0: -12.14 + 1.25*30 + 0.060*20 = 26.56
    assert pf.partial_pet_shadow(30.0, 20.0, np.array(1.0)) == pytest.approx(26.56)


def test_partial_pet_sun_known_value_with_phi_in_radians():
    # u = 1 m/s, so ln(u) = 0: -13.26 + 1.25*30 + 0.011*663 + 0.078*20 + 5.56*sin(PHI)
    # The original code passes PHI = 44 to math.sin, which reads it as 44 RADIANS (sin = 0.0177),
    # not 44 degrees (sin = 0.6947). Kept on purpose to match the original Middelburg map.
    # If this test fails, PHI was changed: that must be a decision with the client, not an accident.
    expected = -13.26 + 1.25*30 + 0.011*663 + 0.078*20 + 5.56*0.0177019
    assert pf.partial_pet_sun(30.0, 20.0, np.array(1.0)) == pytest.approx(expected, abs=1e-5)


def test_more_wind_lowers_the_pet_in_the_shade():
    assert pf.partial_pet_shadow(30.0, 20.0, np.array(4.0)) < pf.partial_pet_shadow(30.0, 20.0, np.array(1.0))


def test_wind_effect_in_the_sun_follows_phi_in_radians():
    # Per unit of ln(u) the sun PET changes by -3.37 + 0.0055*663 - 0.0103*663*sin(PHI).
    # With PHI = 44 radians (as in the original) that is +0.156: more wind gives a slightly HIGHER PET.
    # With 44 degrees it would be -4.467: more wind gives a clearly lower PET, as expected physically.
    # This pins the current behaviour; see test_partial_pet_sun_known_value_with_phi_in_radians.
    slope = -3.37 + 0.0055*663 - 0.0103*663*math.sin(pf.PHI)
    assert slope == pytest.approx(0.1556, abs=1e-3)
    difference = pf.partial_pet_sun(30.0, 20.0, np.array(math.e)) - pf.partial_pet_sun(30.0, 20.0, np.array(1.0))
    assert difference == pytest.approx(slope)


# ---------- per pixel ----------

def test_total_pet_sun_known_value():
    # 30 + 0.546*3 + 1.94*1 = 33.578
    assert pf.total_pet_sun(30.0, 3.0, 1.0) == pytest.approx(33.578)


def test_paved_surface_is_hotter_in_the_sun_than_vegetation():
    paved, green = pf.total_pet_sun(30.0, 3.0, 0.8), pf.total_pet_sun(30.0, 0.4, 0.8)
    assert paved - green == pytest.approx(0.546*(3.0-0.4))


def test_total_pet_shadow_known_values():
    # Open sky (svf 1): no long-wave term. 26.56 + 0.015*1*0.2 = 26.563
    assert pf.total_pet_shadow(26.56, 1.0, 30.0) == pytest.approx(26.563)
    # Closed sky (svf 0): 26.56 + 0.006 * 5.670374419e-8 * (30 + 273.15)^4 = 29.4337
    expected = 26.56 + 0.006*5.670374419e-8*303.15**4
    assert pf.total_pet_shadow(26.56, 0.0, 30.0) == pytest.approx(expected)
    assert expected == pytest.approx(29.4337, abs=1e-3)


def test_combine_uses_the_hillshade_threshold_and_keeps_no_data():
    sun = np.array([40.0, 40.0, 40.0, 40.0], dtype='float32')
    shadow = np.array([30.0, 30.0, 30.0, 30.0], dtype='float32')
    shade = np.array([128.0, 127.0, 0.0, np.nan], dtype='float32')   # 127 itself counts as shade
    result = pf.combine(sun, shadow, shade)
    np.testing.assert_array_equal(result[:3], [40.0, 30.0, 30.0])
    assert np.isnan(result[3])


def test_no_data_stays_no_data():
    nan = np.array([np.nan], dtype='float32')
    assert np.isnan(pf.total_pet_sun(nan, 3.0, 1.0)[0])
    assert np.isnan(pf.total_pet_sun(30.0, nan, 1.0)[0])
    assert np.isnan(pf.total_pet_shadow(26.56, nan, 30.0)[0])


# ---------- shadows ----------

def _dsm(values, resolution=0.5, left=30000.0, top=393750.0):
    ds = gdal.GetDriverByName('MEM').Create('', values.shape[1], values.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((left, resolution, 0, top, 0, -resolution))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(28992)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(values.astype('float32'))
    return ds


def test_sun_position_is_a_summer_afternoon():
    azimuth, altitude = pf.sun_position()
    assert 200 < azimuth < 260          # south-west at 15:00
    assert 40 < altitude < 60


def test_flat_ground_is_in_the_sun():
    dsm = _dsm(np.full((40, 40), 2.0))                     # 20 x 20 m at 0.5 m
    shade = pf.hillshade(dsm, Bounds(30000, 393730, 30020, 393750), 1)
    assert shade.shape == (20, 20)                         # aligned to the 1 m PET grid
    assert np.all(shade[1:-1, 1:-1] > pf.SHADOW_THRESHOLD)


def test_hillshade_edges_have_no_data():
    # computeEdges=False, as in the original shadow_service.py: the outer DSM pixels get no hillshade.
    dsm = _dsm(np.full((40, 40), 2.0))
    shade = pf.hillshade(dsm, Bounds(30000, 393730, 30020, 393750), 0.5)
    assert np.all(np.isnan(shade[0])) and np.all(np.isnan(shade[:, -1]))
    assert not np.any(np.isnan(shade[1:-1, 1:-1]))


def test_steep_slope_facing_away_from_the_sun_is_in_the_shade():
    # Ground that drops 2 m per metre towards the north-east, away from the afternoon sun.
    rows, cols = np.indices((40, 40))
    dsm = _dsm((rows - cols)*1.0)                          # 0.5 m pixels: 1 m per pixel = 2 m per metre
    shade = pf.hillshade(dsm, Bounds(30000, 393730, 30020, 393750), 1)
    assert np.all(shade[1:-1, 1:-1] <= pf.SHADOW_THRESHOLD)