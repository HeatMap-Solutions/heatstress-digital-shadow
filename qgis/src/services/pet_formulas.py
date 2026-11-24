"""The PET heat-stress model in one place, as plain numpy functions.

These are the formulas of the original Middelburg pipeline (pet_service.py and
geojson_service.py on main), based on the Standaard Stresstest Hitte. They are used by
  - the map:        tools/zeeland_tiles.py, per tile
  - the 3x3 update: src/services/local_pet_service.py, per region
so the map and the 3x3 update always calculate the same thing.

Inputs and outputs are numpy arrays (NaN = no data) or plain numbers.
"""
from datetime import datetime
import math

import numpy as np
from osgeo import gdal

from src.configs import area
from src.utils.solar_position import get_solar_position
from src.utils.uhi_lookup_tables import UHILookupTables

# ---------- model constants (values from the original pet_service.py) ----------
T_MIN, T_MAX = 27.2, 29.1                    # daily minimum and maximum temperature (°C)
AVERAGE_WIND = 7.5                           # regional average wind (m/s), used in the UHI
BASE_TEMPERATURE = 28.3                      # air temperature outside the city (°C)
UHI_DATETIME = datetime(2017, 7, 1, 18, 0)   # moment for the UHI factor lookup
RELATIVE_HUMIDITY = 44.0                     # %
PHI = 44.0                                   # kept as in the original: math.sin() reads it as radians, not degrees
Q_GL, Q_DIFF = 663.0, 0.2                    # global and diffuse radiation
SHADOW_THRESHOLD = 127                       # hillshade value above which a pixel counts as in the sun
BOLTZMANN = 5.670374419e-8

# ---------- wind ----------
FF10 = 7.677                                 # wind at 10 m (KNMI)
WIND_PLACEHOLDER = 7.5                       # m/s, for cells without a calculated wind value


def wind_speed_1_2(u_factor):
    """Wind factor u_1_2 from the wind model -> wind speed at 1.2 m in m/s (geschaalde_u_1_2)."""
    speed = FF10 * ((u_factor - 0.0796) * 0.9175 + 0.1254)
    return np.where(speed < 0.5, FF10 - speed, speed)


# ---------- per zone cell ----------

def air_temperature(svf_mean, vegetation_mean):
    """Urban heat island -> air temperature t_a (°C), from the mean SVF and vegetation of a cell."""
    base = ((663 * (T_MAX-T_MIN)**3) / AVERAGE_WIND) ** 0.25
    uhi = (2 - svf_mean - vegetation_mean) * base
    return BASE_TEMPERATURE + uhi * UHILookupTables.get_uhi_factor(UHI_DATETIME)


def wet_bulb_temperature(t_a):
    """Wet-bulb temperature t_w (°C) from the air temperature and the fixed relative humidity."""
    r_h = RELATIVE_HUMIDITY
    return (t_a * math.atan(0.151977 * math.sqrt(r_h + 8.313659)) + np.arctan(t_a + r_h)
            - math.atan(r_h - 1.676331) + 0.00391838 * r_h**1.5 * math.atan(0.023101 * r_h) - 4.686035)


def partial_pet_sun(t_a, t_w, u):
    """Part of the PET in the sun that depends only on the cell (air temperature, wet bulb, wind)."""
    log_u = np.log(u)
    return (-13.26 + 1.25*t_a + 0.011*Q_GL - 3.37*log_u + 0.078*t_w
            + 0.0055*Q_GL*log_u + 5.56*math.sin(PHI) - 0.0103*Q_GL*log_u*math.sin(PHI))


def partial_pet_shadow(t_a, t_w, u):
    """Part of the PET in the shade that depends only on the cell (air temperature, wet bulb, wind)."""
    log_u = np.log(u)
    return -12.14 + 1.25*t_a - 1.47*log_u + 0.060*t_w


# ---------- per pixel ----------

def total_pet_sun(sun_partial, bowen, svf):
    """PET in the sun per pixel: the cell part plus Bowen ratio and sky view factor."""
    return sun_partial + 0.546*bowen + 1.94*svf


def total_pet_shadow(shadow_partial, svf, t_a):
    """PET in the shade per pixel: the cell part plus sky view factor and long-wave radiation."""
    return (shadow_partial + 0.015*svf*Q_DIFF
            + 0.0060*(1-svf)*BOLTZMANN*((t_a+273.15)**4))


def combine(sun_pet, shadow_pet, shade):
    """Final PET: sun PET where the hillshade is bright, shadow PET elsewhere; no data where the hillshade has none."""
    pet = np.where(shade > SHADOW_THRESHOLD, sun_pet, shadow_pet)
    pet[np.isnan(shade)] = np.nan
    return pet


# ---------- shadows ----------

def sun_position():
    """Azimuth and altitude of the sun at the fixed moment in src/configs/area.py."""
    return get_solar_position(area.LAT, area.LON, 'Middelburg', 'Netherlands', 'Europe/Amsterdam',
                              area.SHADOW_DATETIME)


def hillshade(dsm, bounds, pet_resolution):
    """Hillshade (0-255) of a DSM, aligned to the PET grid with nearest neighbour; NaN where there is none.

    `dsm` is a GDAL dataset or a file path; `bounds` has left/bottom/right/top.
    Same settings as gdal:hillshade in the original shadow_service.py.
    """
    azimuth, altitude = sun_position()
    shade = gdal.DEMProcessing('', dsm if isinstance(dsm, gdal.Dataset) else str(dsm), 'hillshade',
                               format='MEM', zFactor=1.0, azimuth=azimuth, altitude=altitude,
                               computeEdges=False)
    aligned = gdal.Warp('', shade, format='MEM', xRes=pet_resolution, yRes=pet_resolution,
                        outputBounds=(bounds.left, bounds.bottom, bounds.right, bounds.top), resampleAlg='near')
    band = aligned.GetRasterBand(1)
    values = band.ReadAsArray().astype('float32')
    nodata = band.GetNoDataValue()
    if nodata is not None:
        values[values == nodata] = np.nan
    return values