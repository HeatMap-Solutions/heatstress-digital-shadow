"""Recalculate selected wind-grid regions and publish a consistent session revision."""
from dataclasses import dataclass
from datetime import datetime
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

from osgeo import gdal, osr

from src.api.models.point import Point
from src.services.pet_service import PETService
from src.services.raster_service import RasterService
from src.services.scenario_grid import Bounds, ScenarioGrid
from src.services.shadow_service import ShadowService
from src.utils.update_qgis_project import update_pet_layer_in_project


STATE_FILE = 'pet-scenario.json'
ALGORITHM_VERSION = 1
# FillNoData searches 10 PET pixels; allow extra context for hillshade and
# nearest-neighbour resampling. Regression tests compare buffered vs full runs.
HALO_PIXELS = 16


@dataclass(frozen=True)
class RasterLayout:
    width: int
    height: int
    transform: tuple
    projection: str

    @classmethod
    def read(cls, path):
        ds = gdal.Open(str(path))
        if ds is None:
            raise ValueError(f'Cannot open scenario raster: {path}')
        result = cls(ds.RasterXSize, ds.RasterYSize, ds.GetGeoTransform(), ds.GetProjection())
        ds = None
        t = result.transform
        crs = osr.SpatialReference(wkt=result.projection)
        expected = osr.SpatialReference()
        expected.ImportFromEPSG(28992)
        if t[2] != 0 or t[4] != 0 or t[1] <= 0 or t[5] >= 0 or not crs.IsSame(expected):
            raise ValueError('Scenario inputs must be north-up EPSG:28992 rasters')
        return result

    @property
    def bounds(self):
        t = self.transform
        return Bounds(t[0], t[3]+self.height*t[5], t[0]+self.width*t[1], t[3])

    def window(self, bounds):
        t = self.transform
        values = ((bounds.left-t[0])/t[1], (bounds.top-t[3])/t[5],
                  (bounds.right-bounds.left)/t[1], (bounds.bottom-bounds.top)/t[5])
        rounded = tuple(round(v) for v in values)
        if any(abs(a-b) > 1e-6 for a, b in zip(values, rounded)):
            raise ValueError('Scenario grid boundaries must align with raster pixels')
        x, y, w, h = rounded
        if min(x, y) < 0 or min(w, h) <= 0 or x+w > self.width or y+h > self.height:
            raise ValueError('Raster window exceeds source coverage')
        return rounded


def normalise_points(points):
    """Stable IDs for new clients; content IDs retain support for old callers."""
    result, occurrences = {}, {}
    for point in points:
        item = point.model_dump(mode='json')
        if not all(math.isfinite(item[k]) for k in ('x', 'y', 'radius', 'height')):
            raise ValueError('Object coordinates, radius and height must be finite')
        if item['radius'] <= 0 or item['height'] < 0:
            raise ValueError('Object radius must be positive and height nonnegative')
        if not item.get('id'):
            digest = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
            occurrences[digest] = occurrences.get(digest, 0)+1
            item['id'] = f'legacy-{digest}-{occurrences[digest]}'
        if item['id'] in result:
            raise ValueError('Object IDs must be unique')
        result[item['id']] = item
    return result


class LocalPETService:
    def __init__(self, data_root='/data', sessions_root=None):
        self.root = Path(data_root)
        self.sessions = Path(sessions_root) if sessions_root else self.root/'server/sessions'
        self.grid_path = self.root/'server/wind-speed-calc.geojson'
        self.inputs = {
            'dsm': self.root/'dsm.TIF',
            'bowen': self.root/'raster/br-reproject.tif',
            'svf': self.root/'raster/svf-reproject-filled.tif',
            'sun': self.root/'uhi/sun-bbox.tif',
            'shadow': self.root/'uhi/shadow-pet.tif',
        }

    def layouts(self):
        layouts = {name: RasterLayout.read(path) for name, path in self.inputs.items()}
        reference = layouts['sun']
        if reference.transform[1:6:4] != (1.0, -1.0):
            raise ValueError('Current scenario calculation requires the existing 1 m PET grid')
        for name in ('bowen', 'svf', 'shadow'):
            if layouts[name].transform[1:6:4] != (1.0, -1.0):
                raise ValueError(f'{name} must use the same 1 m resolution as partial PET')
        coverage = reference.bounds
        for layout in layouts.values():
            coverage = coverage.intersect(layout.bounds)
            if coverage is None:
                raise ValueError('Scenario rasters have no common coverage')
        for layout in layouts.values():
            layout.window(coverage)
        return layouts, coverage

    def calculate_region(self, bounds, points, directory, layouts=None):
        """Same scenario equations, applied to aligned crops in a private workspace.

        Callers include the processing halo and only retain the selected core.
        This is also callable over full coverage for regression comparisons.
        """
        started = time.perf_counter()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        layouts = layouts or self.layouts()[0]
        paths = {}
        for name, source in self.inputs.items():
            paths[name] = str(directory/f'{name}.tif')
            crop = gdal.Translate(paths[name], str(source), srcWin=layouts[name].window(bounds))
            if crop is None:
                raise RuntimeError(f'Failed to crop {name}')
            crop = None
        timings = {'crop': time.perf_counter()-started}
        stage = time.perf_counter()
        # Include crowns/buffers intersecting this window even if their centres
        # are outside it. Preserve request order, as the existing burn operations
        # let later objects overwrite earlier ones where their footprints overlap.
        affecting = [Point(**p) for p in points.values() if bounds.intersect(
            Bounds(p['x'], p['y'], p['x'], p['y']).expand(max(p['radius']+0.3, 3)))]
        raster = RasterService()
        if affecting:
            raster.burn_points_to_raster_pixel_cloud(paths['dsm'], affecting,
                                                     workspace=str(directory), stable_foliage=True)
            raster.burn_points_to_raster(paths['bowen'], affecting, height=0.4,
                                         sameHeight=True, workspace=str(directory))
        timings['objects'] = time.perf_counter()-stage
        stage = time.perf_counter()
        pet = PETService()
        sun = str(directory/'sun-pet.tif')
        pet.calculate_total_pet_sun(paths['sun'], paths['bowen'], paths['svf'], sun, exact_grid=True)
        timings['sun_pet'] = time.perf_counter()-stage
        stage = time.perf_counter()
        dt = datetime(2015, 7, 1, 15)
        shadow = ShadowService().generate_hillshade_maps(paths['dsm'], str(directory),
                                                         51.498, 3.613, dt, dt)
        timings['hillshade'] = time.perf_counter()-stage
        stage = time.perf_counter()
        raw, filled = str(directory/'pet.tif'), str(directory/'pet-filled.tif')
        pet.calculate_total_pet_map(shadow, sun, paths['shadow'], raw,
                                    workspace=str(directory), exact_grid=True)
        raster.fill_nodata_gdal(raw, filled)
        timings['combine_and_fill'] = time.perf_counter()-stage
        if RasterLayout.read(filled).bounds != bounds:
            raise RuntimeError('Calculated patch does not match the requested pixel grid')
        return filled, timings

    def _signature(self, baseline):
        files = [*self.inputs.values(), self.grid_path, baseline]
        # Detect changed inputs without hashing hundreds of MB on every save.
        return {'algorithm': ALGORITHM_VERSION, 'files': {
            str(p): [p.stat().st_size, p.stat().st_mtime_ns] for p in files}}

    @staticmethod
    def _read_project(project):
        with zipfile.ZipFile(project) as archive:
            root = ET.fromstring(archive.read(next(n for n in archive.namelist() if n.endswith('.qgs'))))
            state = json.loads(archive.read(STATE_FILE)) if STATE_FILE in archive.namelist() else None
        for layer in root.findall('./projectlayers/maplayer'):
            if layer.findtext('layername') == 'pet-version-1':
                baseline = (project.parent/layer.findtext('datasource')).resolve()
                return state, baseline
        raise ValueError('Session project has no baseline PET layer')

    def update(self, points, session_id):
        started = time.perf_counter()
        try:
            if not session_id or str(uuid.UUID(session_id)) != session_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError('A valid initialized session is required') from None
        session = self.sessions/session_id
        project = session/'map.qgz'
        if not project.is_file():
            raise ValueError('Initialize the map session before saving objects')
        # flock serializes processes as well as independent request threads.
        with open(session/'.pet-update.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._update_locked(points, project, started)

    def _update_locked(self, points, project, started):
        current = normalise_points(points)
        state, baseline = self._read_project(project)
        signature = self._signature(baseline)
        if state and state['signature'] != signature:
            raise ValueError('Calculation inputs changed; initialize a new session before saving')
        old = state['points'] if state else {}
        previous = project.parent/state['raster'] if state else baseline
        if not previous.is_file():
            raise ValueError('Previous scenario raster is missing; initialize a new session')
        changed = {key for key in old.keys() | current.keys() if old.get(key) != current.get(key)}
        old_order = state.get('order', list(old)) if state else []
        # Reordering can change overlapping heights, even with identical objects.
        if [key for key in old_order if key in current] != [key for key in current if key in old]:
            changed.update(old.keys() & current.keys())
        if state and not changed:
            return {'status': 'success', 'message': 'No object changes', 'output': str(previous),
                    'updated_cells': [], 'timings_seconds': {'total': time.perf_counter()-started}}
        grid = ScenarioGrid(self.grid_path)
        layouts, coverage = self.layouts()
        target_layout = RasterLayout.read(previous)
        if target_layout.transform != layouts['sun'].transform:
            raise ValueError('Baseline PET and calculation inputs must share the 1 m pixel grid')
        target_layout.window(coverage)
        selected = set()
        for snapshot in (old, current):
            for key in changed & snapshot.keys():
                p = snapshot[key]
                # Reject giant footprints rather than silently calculating beyond
                # the agreed one-cell neighbourhood. Also allow the fill halo.
                if max(p['radius']+0.3, 3)+HALO_PIXELS >= min(grid.width, grid.height):
                    raise ValueError('Object radius exceeds the supported 3 x 3 neighbourhood')
                if not (coverage.left <= p['x'] < coverage.right and coverage.bottom < p['y'] <= coverage.top):
                    raise ValueError('Object is outside calculation raster coverage')
                selected.update(grid.neighbours(p['x'], p['y']))
        selected = {key for key in selected if grid.cells[key].intersect(coverage)}
        for key in selected:
            target_layout.window(grid.cells[key].intersect(coverage))
        output = project.parent/f'pet_{uuid.uuid4().hex}_filled.tif'
        timings = {}
        try:
            stage = time.perf_counter()
            shutil.copyfile(previous, output)
            timings['copy_previous'] = time.perf_counter()-stage
            with tempfile.TemporaryDirectory(prefix='pet-work-', dir=project.parent) as temporary:
                for index, region in enumerate(grid.regions(selected)):
                    core = region.intersect(coverage)
                    bounds = core.expand(HALO_PIXELS).intersect(coverage)
                    patch, measured = self.calculate_region(bounds, current, Path(temporary)/str(index), layouts)
                    for key, seconds in measured.items():
                        timings[key] = timings.get(key, 0)+seconds
                    stage = time.perf_counter()
                    self._write_patch(output, patch, core)
                    timings['write_patch'] = timings.get('write_patch', 0)+time.perf_counter()-stage
            if RasterLayout.read(output) != target_layout:
                raise RuntimeError('Session output grid changed unexpectedly')
            new_state = {'signature': signature, 'points': current, 'order': list(current),
                         'raster': output.name}
            stage = time.perf_counter()
            # State and published raster pointer share ONE atomic QGZ replacement.
            update_pet_layer_in_project(str(project), str(output), output.stem,
                                        scenario_state=new_state)
            timings['publish'] = time.perf_counter()-stage
        except Exception:
            output.unlink(missing_ok=True)
            raise
        timings['total'] = time.perf_counter()-started
        return {'status': 'success', 'message': f'Updated {len(selected)} grid cells',
                'output': str(output), 'updated_cells': [list(key) for key in sorted(selected)],
                'timings_seconds': timings}

    @staticmethod
    def _write_patch(output, patch, core):
        source = gdal.Open(str(patch))
        target = gdal.Open(str(output), gdal.GA_Update)
        sx, sy, w, h = RasterLayout.read(patch).window(core)
        tx, ty, _, _ = RasterLayout.read(output).window(core)
        source_band, target_band = source.GetRasterBand(1), target.GetRasterBand(1)
        values = source_band.ReadAsArray(sx, sy, w, h)
        # Preserve the target's NoData convention, rather than introducing a
        # second sentinel that would be rendered as a real temperature.
        source_nodata, target_nodata = source_band.GetNoDataValue(), target_band.GetNoDataValue()
        if source_nodata != target_nodata:
            if target_nodata is None:
                raise ValueError('Baseline PET must declare its NoData value')
            values[values == source_nodata] = target_nodata
        if target_band.WriteArray(values, tx, ty) != gdal.CE_None:
            raise RuntimeError('Failed to write the calculated PET patch')
        for key in list(target_band.GetMetadata()):
            if key.startswith('STATISTICS_'):
                target_band.SetMetadataItem(key, None)
        target.FlushCache()
        source = target = None
