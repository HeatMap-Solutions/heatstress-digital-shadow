"""The map calculation still gives exactly the reference run.

Recalculates one tile with src/services/pet_map_service.py (the same code as
tools/generate_pet_map.py) and compares it pixel for pixel with the same tile of
the reference run: the PET and the three layers the 3x3 update reads. Any change
to the formulas, the inputs or the tiling shows up here.

Needs the real data in /data and takes about a minute. Skipped when the reference
run is not there. Run in the qgis container:

    docker exec -w /app qgis python3 -m pytest tests/test_pet_map_regression.py -v

Compare with another run:  -e PET_REFERENCE_RUN=/data/zeeland-tiles/<run>  (docker exec option)
"""
import os
from pathlib import Path

import numpy as np
from osgeo import gdal
import pytest

from src.services.pet_map_service import generate

REFERENCE = Path(os.environ.get('PET_REFERENCE_RUN', '/data/zeeland-tiles/zeeland-1m-3x3'))
TILE = 't_010_013.tif'                     # x 30000-32800, y 388150-390950 (RD New), south of Middelburg
BBOX = (30100, 388250, 30200, 388350)      # lies inside that one tile only


@pytest.fixture(scope='module')
def run(tmp_path_factory):
    if not (REFERENCE/'tiles'/TILE).exists():
        pytest.skip(f'Reference run not found: {REFERENCE}')
    out = tmp_path_factory.mktemp('pet-map-regression')
    summary = generate(out, 1.0, 2800, BBOX)
    assert summary['tiles_done'] == '1/1'
    return out


@pytest.mark.parametrize('folder', ['tiles', 'tiles-svf', 'tiles-sun-partial', 'tiles-shadow-pet'])
def test_tile_is_identical_to_the_reference_run(run, folder):
    new, old = gdal.Open(str(run/folder/TILE)), gdal.Open(str(REFERENCE/folder/TILE))
    assert new.GetGeoTransform() == old.GetGeoTransform()
    assert new.GetRasterBand(1).GetNoDataValue() == old.GetRasterBand(1).GetNoDataValue()
    np.testing.assert_array_equal(new.ReadAsArray(), old.ReadAsArray())