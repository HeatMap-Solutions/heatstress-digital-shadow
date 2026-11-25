"""One place for the Zeeland PET map settings: where the data is, and the fixed sun position.

Everything that needs a data path reads it from here:
  - src/services/pet_map_service.py   (calculates the map, tile by tile; run with tools/generate_pet_map.py)
  - tools/publish_pet_map.py          (publishes a run in the app)
  - tools/build_wind_grid.py          (wind grid for the 3x3 update)
  - src/services/local_pet_service.py (the 3x3 update)
To use other data, change it here and not in those files.

Paths are as seen inside the qgis container. PET_DATA_ROOT overrides the data
root, e.g. for tests that build a small data folder of their own.
"""
from datetime import datetime
import os
from pathlib import Path

# ---------- folders ----------
DATA_ROOT = Path(os.environ.get('PET_DATA_ROOT', '/data'))
SOURCE = DATA_ROOT/'zeeland'                 # source data for Zeeland (LGN, AHN, SVF, wind, boundary)
INPUTS = DATA_ROOT/'zeeland-tiles/inputs'    # prepared inputs, made in QGIS following the wiki
RUNS = DATA_ROOT/'zeeland-tiles'             # one folder per tiles run (pet.vrt, svf.vrt, ...)
SERVER = DATA_ROOT/'server'                  # QGIS Server projects, served VRTs, styles and sessions

# ---------- source data ----------
LGN = f'/vsizip/{SOURCE}/sources/lgn2018/LGN2018.zip/LGN2018/LGN2018.tif'   # land use, only for the land mask
DSM = SOURCE/'acquisition-20261005/elevation-dsm/native.vrt'                # AHN3 surface model, 0.5 m
SVF_MANIFEST = SOURCE/'sources/svf-nl-3/manifest.json'                      # national sky view factor tiles
BOUNDARY = SOURCE/'boundary/zeeland-rd.gpkg'                                # province boundary

# ---------- prepared inputs (QGIS Raster Calculator on LGN2018, 1 m, see the wiki) ----------
BOWEN = INPUTS/'bowen_zeeland_lgn2018_1m.tif'             # 3 = paved, 0.4 = vegetation/water
VEGETATION = INPUTS/'vegetation_zeeland_lgn2018_1m.tif'   # 0 = paved, 1 = vegetation/water

# ---------- wind (from the wind team) ----------
WIND = SOURCE/'sources/wind/zeeland-wind.gpkg'
WIND_LAYER = 'wind_zeeland_land_candidate'
WIND_GRID = SOURCE/'sources/wind/zeeland-wind-grid.geojson'   # made from WIND by tools/build_wind_grid.py

# ---------- publishing ----------
STYLE = SERVER/'styles/new-default.qml'   # same continuous PET colour ramp as the original Middelburg map

# ---------- sun position for the shadows (same moment for the map and the 3x3 update) ----------
SHADOW_DATETIME = datetime(2015, 7, 1, 15, 0)
LAT, LON = 51.498, 3.613

# Reference maps to compare with (tools/compare_hittestresskaart.py): the province's GeoServer, in RD New.
REFERENCE_WMS = 'https://opengeodata.zeeland.nl/geoserver/{workspace}/wms'
REFERENCE_LAYERS = {
    'klimaat:Hittestress_1_juli_2015': 'PET hittekaart Zeeland, TAUW 2023, weather of 1 July 2015 (same day as our model)',
    'rasters:geokma_httstrkrtrst': 'PET hittekaart Provincie Zeeland (Hittestresskaart in the dataportaal)',
}