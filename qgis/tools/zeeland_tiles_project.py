#!/usr/bin/env python3
"""Point the app's PET layer at a tiles run.

Writes /data/server/starting-map.qgz and /data/server/map.qgz with one layer,
'pet-version-1', reading the run's tiles through a VRT in /data/server.

    python3 /app/tools/zeeland_tiles_project.py /data/zeeland-tiles/zeeland-1m/pet.vrt
"""
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.configs.preflight import init_qgis
from src.utils.pet_tiles import build_vrt

SERVER = Path('/data/server')
STYLE = SERVER/'heatstress-style.qml'


def main(vrt):
    app = init_qgis()
    from qgis.core import QgsProject, QgsRasterLayer

    # Sessions copy this project two folders deeper and only fix './' paths,
    # so the layer must point at a VRT inside data/server itself.
    run = Path(vrt).resolve().parent
    tiles = sorted(p for p in (run/'tiles').glob('t_*.tif') if '.partial' not in p.name)
    served = build_vrt(SERVER/f'{run.name}.vrt', tiles)
    layer = QgsRasterLayer(str(served), 'pet-version-1')
    if not layer.isValid():
        raise SystemExit(f'QGIS cannot open {vrt}')
    message, ok = layer.loadNamedStyle(str(STYLE))
    if not ok:
        raise SystemExit(f'Could not load {STYLE}: {message}')
    project = QgsProject()
    project.addMapLayer(layer)
    project.setCrs(layer.crs())
    project.writeEntryBool('Paths', '/Absolute', False)  # relative paths: works under /data and /io/data
    target = SERVER/'starting-map.qgz'
    if not project.write(str(target)):
        raise SystemExit(f'Could not write {target}')
    shutil.copyfile(target, SERVER/'map.qgz')
    print(f'pet-version-1 -> {served} ({len(tiles)} tiles)')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
    