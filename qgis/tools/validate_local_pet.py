"""Opt-in real-data check. Writes only to a new output directory, never to inputs.

Run from /app in the QGIS container:
  python3 tools/validate_local_pet.py --output /tmp/local-pet-check
The output directory must not exist. Timings are exploratory, not acceptance targets.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import resource
import shutil
import sys
import time
import uuid
import zipfile

import numpy as np
from osgeo import gdal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.configs.preflight import init_qgis
from src.api.models.point import Point
from src.services.local_pet_service import LocalPETService, RasterLayout, normalise_points
from src.services.scenario_grid import ScenarioGrid
from src.services.raster_service import RasterService
from src.services.pet_service import PETService
from src.services.shadow_service import ShadowService
from qgis.core import Qgis


def fingerprint(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def full_reference(service, points, output):
    """The pre-change scenario sequence, with private files and stable foliage.

    Keep the original PROJWIN and alignment behaviour for an independent check
    of the local path's exact-grid handling. Full generation is not invoked.
    """
    output.mkdir()
    raster, pet = RasterService(), PETService()
    objects = [Point(**p) for p in normalise_points(points).values()]
    dsm, bowen = str(output/'dsm.tif'), str(output/'bowen.tif')
    started = time.perf_counter()
    if objects:
        raster.burn_points_to_raster_pixel_cloud(str(service.inputs['dsm']), objects,
            output_path=dsm, workspace=str(output), stable_foliage=True)
        raster.burn_points_to_raster(str(service.inputs['bowen']), objects,
            output_path=bowen, height=.4, sameHeight=True, workspace=str(output))
    else:
        shutil.copyfile(service.inputs['dsm'], dsm)
        shutil.copyfile(service.inputs['bowen'], bowen)
    sun = str(output/'sun.tif')
    pet.calculate_total_pet_sun(str(service.inputs['sun']), bowen, str(service.inputs['svf']), sun)
    dt = datetime(2015, 7, 1, 15)
    shadow = ShadowService().generate_hillshade_maps(dsm, str(output), 51.498, 3.613, dt, dt)
    raw, filled = str(output/'pet.tif'), str(output/'pet-filled.tif')
    pet.calculate_total_pet_map(shadow, sun, str(service.inputs['shadow']), raw, workspace=str(output))
    raster.fill_nodata_gdal(raw, filled)
    return filled, time.perf_counter()-started


def compare(actual_path, expected_path, cells=None, grid=None):
    actual, expected = gdal.Open(str(actual_path)), gdal.Open(str(expected_path))
    layout = RasterLayout.read(actual_path)
    assert layout == RasterLayout.read(expected_path), 'Comparison raster grids differ'
    counts = dict(selected_pixels=0, changed_inside=0, changed_outside=0,
                  validity_mismatches_inside=0, max_finite_difference_inside=0.0)
    windows = [layout.window(grid.cells[tuple(key)].intersect(layout.bounds)) for key in cells] if cells is not None else None
    for y in range(0, actual.RasterYSize, 256):
        h = min(256, actual.RasterYSize-y)
        a, b = actual.ReadAsArray(0, y, actual.RasterXSize, h), expected.ReadAsArray(0, y, expected.RasterXSize, h)
        selected = np.ones(a.shape, dtype=bool) if windows is None else np.zeros(a.shape, dtype=bool)
        for x, top, w, height in windows or []:
            start, end = max(top, y), min(top+height, y+h)
            if start < end:
                selected[start-y:end-y, x:x+w] = True
        an, bn = actual.GetRasterBand(1).GetNoDataValue(), expected.GetRasterBand(1).GetNoDataValue()
        av, bv = np.isfinite(a) & (a != an), np.isfinite(b) & (b != bn)
        equal = (a == b) | (np.isnan(a) & np.isnan(b))
        counts['selected_pixels'] += int(selected.sum())
        counts['changed_inside'] += int((~equal & selected).sum())
        counts['changed_outside'] += int((~equal & ~selected).sum())
        counts['validity_mismatches_inside'] += int(((av != bv) & selected).sum())
        finite = av & bv & selected
        if finite.any():
            counts['max_finite_difference_inside'] = max(counts['max_finite_difference_inside'], float(np.max(np.abs(a[finite]-b[finite]))))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='/data')
    parser.add_argument('--output', required=True)
    parser.add_argument('--revision', default='unrecorded')
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    app = init_qgis()
    service = LocalPETService(args.data, output/'sessions')
    baseline = service.root/'server/pet-version-1.tif'
    sources = [*service.inputs.values(), service.grid_path, baseline]
    before = {str(p): fingerprint(p) for p in sources}
    objects = [Point(id='validation-tree-1', x=31740, y=391420, height=15, radius=5, geometry='circle')]
    sid = str(uuid.uuid4())
    session = service.sessions/sid
    session.mkdir(parents=True)
    with zipfile.ZipFile(session/'map.qgz', 'w') as archive:
        archive.writestr('map.qgs', f'<qgis><projectlayers><maplayer><id>base</id><layername>pet-version-1</layername><datasource>{baseline}</datasource></maplayer></projectlayers></qgis>')
    # Sequential runs; cache is not cleared. Source hashes and QGIS startup are
    # outside timing. Full reference excludes QGZ publication; local includes it.
    full, full_seconds = full_reference(service, objects, output/'full-reference')
    local = service.update(objects, sid)
    grid = ScenarioGrid(service.grid_path)
    comparison = compare(local['output'], full, local['updated_cells'], grid)
    preservation = compare(local['output'], baseline, local['updated_cells'], grid)
    repeat = service.update(objects, sid)
    empty_full, empty_seconds = full_reference(service, [], output/'empty-reference')
    removed = service.update([], sid)
    removal_comparison = compare(removed['output'], empty_full, removed['updated_cells'], grid)
    removal_preservation = compare(removed['output'], local['output'], removed['updated_cells'], grid)
    report = {
        'revision': args.revision, 'date_utc': datetime.now(timezone.utc).isoformat(),
        'platform': platform.platform(), 'qgis': Qgis.QGIS_VERSION,
        'gdal': gdal.VersionInfo('--version'), 'source_fingerprints': before,
        'cache_state': 'not cleared; full reference runs before local',
        'weather': 'existing prepared PET inputs; hillshade 2015-07-01 15:00 Europe/Amsterdam, 51.498 N 3.613 E',
        'objects': [p.model_dump(mode='json') for p in objects],
        'full_reference_seconds': full_seconds, 'empty_reference_seconds': empty_seconds,
        'local': local, 'repeat': repeat, 'removal': removed,
        'local_vs_full': comparison, 'outside_preservation': preservation,
        'removal_vs_empty_full': removal_comparison, 'removal_outside_preservation': removal_preservation,
        'supplied_baseline_vs_empty_full': compare(baseline, empty_full),
        'source_inputs_unchanged': before == {str(p): fingerprint(p) for p in sources},
        'process_peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'limitation': 'Single exploratory sequential service-level comparison; no HTTP/browser timing, no per-stage resource sampling, no scientific accuracy claim.',
    }
    (output/'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ('full_reference_seconds', 'local_vs_full',
        'removal_vs_empty_full', 'source_inputs_unchanged')}, indent=2))
    assert comparison['changed_inside'] == 0, 'Local differs from full reference'
    assert comparison['validity_mismatches_inside'] == 0
    assert preservation['changed_outside'] == 0
    assert removal_comparison['changed_inside'] == 0
    assert removal_preservation['changed_outside'] == 0
    assert report['source_inputs_unchanged']
    assert repeat['updated_cells'] == []
    # Keep QgsApplication alive until all calculations have finished.
    assert app is not None


if __name__ == '__main__':
    main()
