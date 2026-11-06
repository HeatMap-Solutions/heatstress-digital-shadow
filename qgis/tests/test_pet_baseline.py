"""Numerical compatibility with original JRCZ main, not a physics validation."""
from pathlib import Path
import numpy as np
import pytest

from src.services.local_pet_service import LocalPETService, normalise_points
from test_local_pet import raster, write_grid, point, read


@pytest.mark.parametrize('extreme', [False, True])
def test_original_main_tree_pet_snapshot(tmp_path, qgis_app, extreme):
    (tmp_path/'server').mkdir()
    write_grid(tmp_path/'server/wind-speed-calc.geojson', 3, 3)
    yy, xx = np.indices((480, 240))
    raster(tmp_path/'dsm.TIF', (2+xx*.01+yy*.002).astype('float32'), .5)
    for name, value in [('raster/br-reproject.tif', .7),
                        ('raster/svf-reproject-filled.tif', .6),
                        ('uhi/sun-bbox.tif', 30), ('uhi/shadow-pet.tif', 24)]:
        values = np.full((240, 120), value, dtype='float32')
        sentinel = float(np.finfo('float32').max) if extreme else -9999.
        values[110:114, 50:54] = sentinel
        if extreme:
            values[111, 51] = np.nan
        raster(tmp_path/name, values, nodata=sentinel)
    service = LocalPETService(tmp_path)
    actual, _ = service.calculate_region(service.layouts()[1],
        normalise_points([point(col=1, row=1)]), tmp_path/'output')
    fixtures = Path(__file__).parent/'fixtures'
    references = [fixtures/f'baseline-{extreme}.npz']
    if not extreme:
        references.append(fixtures/'baseline-active-False.npz')
    # Only the core is retained by local updates. Original PROJWIN produces an
    # extra east/south border; hillshade/fill at crop edges need the 16 m halo.
    core = read(actual)[16:-16, 16:-16]
    # Original main itself produced two finite-sentinel fill outcomes across
    # fresh and long-running containers. Accept either whole reference core,
    # never an arbitrary per-pixel mixture. See fixture provenance.
    for reference in references:
        with np.load(reference) as snapshot:
            expected = snapshot['expected'][16:-16, 16:-16]
        if np.array_equal(core, expected, equal_nan=True):
            return
    np.testing.assert_array_equal(core, expected)
