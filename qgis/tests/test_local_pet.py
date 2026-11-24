"""Tests for the 3x3 update (src/services/local_pet_service.py).

Offline: a small synthetic data set (7 x 7 wind cells of 40 x 80 m) is built in a
temporary folder, so no PDOK and no Zeeland data are needed. Run in the qgis container:

    docker exec -w /app qgis python3 -m pytest tests/test_local_pet.py -v

The main check (assert_matches_full) is that recalculating only the 3x3 cells around
a tree gives exactly the same pixels as recalculating the whole area with that tree,
and that every pixel outside those cells stays as it was.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid
import zipfile

import numpy as np
from osgeo import gdal, osr
import pytest

from src.api.models.point import Point
from src.services.local_pet_service import LocalPETService, PATCH_NODATA, RasterLayout, normalise_points
from src.services.scenario_grid import ScenarioGrid

# The same inputs the real service reads from a tiles run (see LocalPETService._inputs_for).
INPUT_FILES = {'dsm': 'dsm.tif', 'bowen': 'bowen.tif', 'svf': 'svf.tif',
               'sun': 'sun-partial.tif', 'shadow': 'shadow-pet.tif'}


def write_grid(path, cols=7, rows=7):
    features = []
    for col in range(cols):
        for row in range(rows):
            left, top = 30000+40*col, 393750-80*row
            right, bottom = left+40, top-80
            features.append({'type': 'Feature', 'properties': {
                'col_index': col, 'row_index': row, 'left': left, 'right': right,
                'top': top, 'bottom': bottom}, 'geometry': {'type': 'Polygon', 'coordinates': [[
                    [left, top], [right, top], [right, bottom], [left, bottom], [left, top]]]}})
    path.write_text(json.dumps({'type': 'FeatureCollection', 'features': features,
                               'crs': {'properties': {'name': 'urn:ogc:def:crs:EPSG::28992'}}}))


def raster(path, values, resolution=1, nodata=-9999):
    path.parent.mkdir(parents=True, exist_ok=True)
    ds = gdal.GetDriverByName('GTiff').Create(str(path), values.shape[1], values.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((30000, resolution, 0, 393750, 0, -resolution))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(28992)
    ds.SetProjection(crs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(nodata)
    ds.GetRasterBand(1).WriteArray(values)
    ds = None


def read(path):
    ds = gdal.Open(str(path))
    return ds.ReadAsArray()


def point(col=3, row=3, id='tree-a', **changes):
    data = dict(id=id, x=30000+40*col+20, y=393750-80*row-40,
                radius=5, height=15, geometry='circle')
    return Point(**(data | changes))


def make_service(root, sessions=None):
    """LocalPETService on the synthetic data in `root` instead of the published Zeeland run."""
    service = LocalPETService(sessions or root/'sessions')
    inputs = {name: root/file for name, file in INPUT_FILES.items()}
    service.grid_path = root/'grid.geojson'
    service.inputs = inputs
    service._inputs_for = lambda baseline: inputs
    return service


def make_session(sessions, baseline):
    """A session folder with a map.qgz whose 'pet-version-1' layer is `baseline` (as the backend makes it)."""
    sid = str(uuid.uuid4())
    folder = sessions/sid
    folder.mkdir(parents=True)
    xml = f'''<qgis><projectlayers><maplayer><id>base</id><layername>pet-version-1</layername>
    <datasource>{baseline}</datasource></maplayer></projectlayers><layer-tree-group>
    <layer-tree-layer id="base" name="pet-version-1"/><custom-order><item>base</item></custom-order>
    </layer-tree-group></qgis>'''
    with zipfile.ZipFile(folder/'map.qgz', 'w') as archive:
        archive.writestr('map.qgs', xml)
    return sid, folder


@pytest.fixture(scope='module')
def prepared(tmp_path_factory, qgis_app):
    """Synthetic inputs plus the shared map ('baseline'), calculated from them without trees."""
    root = tmp_path_factory.mktemp('prepared-pet')
    write_grid(root/'grid.geojson')
    yy, xx = np.indices((1120, 560))
    dsm = (2+xx*0.01+yy*0.002).astype('float32')
    dsm[300:350, 230:270] += 10
    raster(root/INPUT_FILES['dsm'], dsm, .5)
    for name, value in [('bowen', .7), ('svf', .6), ('sun', 30), ('shadow', 24)]:
        values = np.full((560, 280), value, dtype='float32')
        # NoData stripe crosses a selected region's southern boundary.
        values[397:402, 80:200] = -9999
        raster(root/INPUT_FILES[name], values)
    service = make_service(root)
    baseline, _ = service.calculate_region(service.layouts()[1], {}, root/'baseline-work')
    shutil.copyfile(baseline, root/'pet-baseline.tif')
    return root


@pytest.fixture
def baseline(prepared):
    return prepared/'pet-baseline.tif'


@pytest.fixture
def session(prepared, baseline, tmp_path):
    sessions = tmp_path/'sessions'
    sid, folder = make_session(sessions, baseline)
    return make_service(prepared, sessions), sid, folder


def assert_matches_full(service, result, objects, tmp_path, previous, baseline):
    """Selected cells equal a full recalculation; all other pixels equal `previous`; same grid."""
    full, _ = service.calculate_region(service.layouts()[1], normalise_points(objects), tmp_path/'reference')
    expected, actual, prior, base = read(full), read(result['output']), read(previous), read(baseline)
    # The shared map's NoData (water, outside the area) is never filled in by an update.
    expected = np.where(base == PATCH_NODATA, PATCH_NODATA, expected)
    selected = np.zeros(actual.shape, dtype=bool)
    grid = ScenarioGrid(service.grid_path)
    layout = RasterLayout.read(result['output'])
    for key in result['updated_cells']:
        x, y, w, h = layout.window(grid.cells[tuple(key)])
        selected[y:y+h, x:x+w] = True
    np.testing.assert_array_equal(actual[selected], expected[selected])
    np.testing.assert_array_equal(actual[~selected], prior[~selected])
    before = RasterLayout.read(previous)
    assert (layout.width, layout.height, layout.transform) == (before.width, before.height, before.transform)


def test_grid_indices_edges_and_exact_union(tmp_path):
    path = tmp_path/'grid.json'
    write_grid(path)
    grid = ScenarioGrid(path)
    for (col, row), b in grid.cells.items():
        assert grid.locate(b.left+20, b.top-40) == (col, row)
    assert grid.locate(30040, 393670) == (1, 1)
    assert len(grid.neighbours(30020, 393710)) == 4
    assert len(grid.neighbours(30020, 393470)) == 6
    assert len(grid.neighbours(30140, 393470)) == 9
    with pytest.raises(ValueError):
        grid.locate(29999, 393710)
    selected = grid.neighbours(30020, 393710) | grid.neighbours(30260, 393230)
    regions = grid.regions(selected)
    covered = {key for key, b in grid.cells.items() if any(b.intersect(r) for r in regions)}
    assert covered == selected
    assert sum((b.right-b.left)*(b.top-b.bottom) for b in regions) == len(selected)*40*80


def test_grid_rejects_inconsistent_geometry(tmp_path):
    path = tmp_path/'grid.json'
    write_grid(path)
    data = json.loads(path.read_text())
    data['features'][0]['properties']['left'] += 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='geometry'):
        ScenarioGrid(path)


def test_point_ids_and_invalid_values():
    anonymous = point(id=None)
    assert normalise_points([anonymous]) == normalise_points([anonymous])
    assert len(normalise_points([anonymous, anonymous])) == 2
    with pytest.raises(ValueError, match='unique'):
        normalise_points([point(), point()])
    with pytest.raises(ValueError, match='finite'):
        normalise_points([point(x=float('nan'))])


def test_single_tree_exact_against_full_and_noop(session, baseline, tmp_path):
    svc, sid, folder = session
    result = svc.update([point()], sid)
    assert len(result['updated_cells']) == 9
    assert_matches_full(svc, result, [point()], tmp_path, baseline, baseline)
    before = (folder/'map.qgz').read_bytes()
    repeated = svc.update([point()], sid)
    assert repeated['updated_cells'] == []
    assert repeated['output'] == result['output']
    assert (folder/'map.qgz').read_bytes() == before


@pytest.mark.parametrize('objects', [
    [point(x=30120, y=393510)],  # exactly on shared cell boundaries
    [point(col=0, row=0, x=30001, y=393749)],
    [point(col=6, row=6, x=30279, y=393191)],
    [point(col=0, row=0), point(col=6, row=6, id='tree-b')],
    [point(), point(id='tree-b', x=30143)],
])
def test_boundaries_distant_and_overlapping_trees(session, baseline, tmp_path, objects):
    svc, sid, _ = session
    result = svc.update(objects, sid)
    assert_matches_full(svc, result, objects, tmp_path, baseline, baseline)


def test_move_property_change_and_delete(session, baseline, tmp_path):
    svc, sid, _ = session
    original = svc.update([point(col=1, row=1)], sid)
    moved = point(col=5, row=5, height=20)
    updated = svc.update([moved], sid)
    assert len(updated['updated_cells']) == 18
    assert_matches_full(svc, updated, [moved], tmp_path/'move', original['output'], baseline)
    deleted = svc.update([], sid)
    assert len(deleted['updated_cells']) == 9
    assert_matches_full(svc, deleted, [], tmp_path/'delete', updated['output'], baseline)
    np.testing.assert_array_equal(read(deleted['output']), read(baseline))


def test_shared_map_is_never_written(session, baseline):
    svc, sid, _ = session
    before, modified = baseline.read_bytes(), baseline.stat().st_mtime_ns
    svc.update([point()], sid)
    svc.update([point(col=5, row=5)], sid)
    svc.update([], sid)
    assert baseline.read_bytes() == before
    assert baseline.stat().st_mtime_ns == modified


def test_session_map_is_the_shared_map_plus_small_patches(session):
    svc, sid, folder = session
    result = svc.update([point()], sid)
    assert Path(result['output']).suffix == '.vrt'
    patches = list(folder.glob('patch_*.tif'))
    assert len(patches) == 1                                    # one region: the 3x3 cells
    layout = RasterLayout.read(patches[0])
    assert (layout.width, layout.height) == (3*40, 3*80)        # only the 3x3 cells, 1 m pixels
    patch = gdal.Open(str(patches[0]))
    assert patch.GetRasterBand(1).GetOverviewCount() == 4       # NEAREST overviews, like the tiles
    patch = None


def test_update_keeps_the_shared_maps_no_data(prepared, baseline, tmp_path):
    """A tree next to water: the update must not fill (bleed) PET values into the water."""
    water = tmp_path/'pet-with-water.tif'
    shutil.copyfile(baseline, water)
    values = read(water)
    values[250:260, 130:140] = PATCH_NODATA                     # inside the 3x3 cells around point()
    ds = gdal.Open(str(water), gdal.GA_Update)
    ds.GetRasterBand(1).WriteArray(values)
    ds = None
    sid, _ = make_session(tmp_path/'sessions', water)
    svc = make_service(prepared, tmp_path/'sessions')
    result = svc.update([point()], sid)
    assert np.all(read(result['output'])[250:260, 130:140] == PATCH_NODATA)
    assert_matches_full(svc, result, [point()], tmp_path, water, water)


def test_failure_keeps_published_state_and_previous_raster(session, monkeypatch):
    svc, sid, folder = session
    first = svc.update([point()], sid)
    project_before = (folder/'map.qgz').read_bytes()
    raster_before = Path(first['output']).read_bytes()
    patches_before = sorted(folder.glob('patch_*.tif'))
    def fail(*args, **kwargs):
        raise RuntimeError('simulated calculation failure')
    monkeypatch.setattr(svc, 'calculate_region', fail)
    with pytest.raises(RuntimeError, match='simulated'):
        svc.update([point(height=21)], sid)
    assert (folder/'map.qgz').read_bytes() == project_before
    assert Path(first['output']).read_bytes() == raster_before
    assert len(list(folder.glob('pet_*.vrt'))) == 1
    assert sorted(folder.glob('patch_*.tif')) == patches_before
    assert not list(folder.glob('pet-work-*'))


def test_invalid_requests_do_not_publish(session):
    svc, sid, folder = session
    before = (folder/'map.qgz').read_bytes()
    for objects in ([point(x=0)], [point(radius=100)], [point(radius=-1)]):
        with pytest.raises(ValueError):
            svc.update(objects, sid)
    with pytest.raises(ValueError, match='session'):
        svc.update([], '../invalid')
    assert (folder/'map.qgz').read_bytes() == before


def test_empty_first_save_and_restart(session, prepared):
    svc, sid, folder = session
    result = svc.update([], sid)
    assert result['updated_cells'] == []
    with zipfile.ZipFile(folder/'map.qgz') as archive:
        saved = json.loads(archive.read('pet-scenario.json'))
    assert saved['points'] == {}
    restarted = make_service(prepared, svc.sessions)
    assert restarted.update([], sid)['output'] == result['output']


def test_input_signature_change_requires_new_session(session, monkeypatch):
    svc, sid, _ = session
    svc.update([], sid)
    monkeypatch.setattr(svc, '_signature', lambda baseline: {'algorithm': -1})
    with pytest.raises(ValueError, match='inputs changed'):
        svc.update([point()], sid)


def test_republishing_the_map_requires_new_session(prepared, baseline, tmp_path):
    """What happens after tools/publish_pet_map.py: the shared map file changes, old sessions are refused."""
    published = tmp_path/'pet-published.tif'
    shutil.copyfile(baseline, published)
    sid, _ = make_session(tmp_path/'sessions', published)
    svc = make_service(prepared, tmp_path/'sessions')
    svc.update([], sid)
    later = published.stat().st_mtime_ns + 10**9
    os.utime(published, ns=(later, later))                    # same content, new modification time
    with pytest.raises(ValueError, match='inputs changed'):
        svc.update([point()], sid)


def test_publication_failure_does_not_commit_objects(session, monkeypatch):
    import src.services.local_pet_service as module
    svc, sid, folder = session
    svc.update([], sid)
    before = (folder/'map.qgz').read_bytes()
    def fail(*args, **kwargs):
        raise RuntimeError('simulated publication failure')
    monkeypatch.setattr(module, 'update_pet_layer_in_project', fail)
    with pytest.raises(RuntimeError, match='publication'):
        svc.update([point()], sid)
    assert (folder/'map.qgz').read_bytes() == before
    assert len(list(folder.glob('pet_*.vrt'))) == 1
    assert not list(folder.glob('patch_*.tif'))


def test_same_session_updates_are_serialized(session, monkeypatch):
    svc, sid, _ = session
    start = threading.Barrier(2)
    active, maximum = 0, 0
    def inside(*args):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        time.sleep(.05)
        active -= 1
    monkeypatch.setattr(svc, '_update_locked', inside)
    def submit():
        start.wait(timeout=5)
        return svc.update([], sid)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)
    assert maximum == 1


def test_separate_sessions_retain_independent_state(session, baseline):
    svc, sid, folder = session
    second_id = str(uuid.uuid4())
    second = svc.sessions/second_id
    second.mkdir()
    shutil.copyfile(folder/'map.qgz', second/'map.qgz')
    first = svc.update([point()], sid)
    untouched = svc.update([], second_id)
    np.testing.assert_array_equal(read(untouched['output']), read(baseline))
    assert svc.update([point()], sid)['output'] == first['output']


def test_reordered_overlapping_objects_are_recalculated(session, baseline, tmp_path):
    svc, sid, _ = session
    objects = [point(), point(id='tree-b', height=2)]
    first = svc.update(objects, sid)
    reordered = list(reversed(objects))
    second = svc.update(reordered, sid)
    assert len(second['updated_cells']) == 9
    assert_matches_full(svc, second, reordered, tmp_path, first['output'], baseline)