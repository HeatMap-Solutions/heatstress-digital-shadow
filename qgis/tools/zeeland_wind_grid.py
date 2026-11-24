#!/usr/bin/env python3
"""Zeeland scenario grid for the 3 x 3 update, made from the wind team's GeoPackage.

The 3 x 3 update (src/services/scenario_grid.py) needs the same kind of file as
Middelburg's /data/server/wind-speed-calc.geojson: one RECTANGLE per wind cell,
fields col_index/row_index/left/bottom/right/top, EPSG:28992, and row_index
counting from north to south. The wind GeoPackage has the same cells, but clipped
to land, with column_id/row_id and row_id counting from south to north. This
script converts it; the wind values themselves are copied along as information.

    python3 /app/tools/zeeland_wind_grid.py        # paths from src/configs/area.py
"""

import json
from pathlib import Path
import sys

from osgeo import ogr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.configs import area

LAYER = area.WIND_LAYER
CELL_W, CELL_H = 250, 125   # wind cell size in metres; column_id * 250 = left, row_id * 125 = bottom


def main(source, target):
    ogr.UseExceptions()
    data = ogr.Open(source)
    layer = data.GetLayerByName(LAYER)
    rows = [(f.GetField('column_id'), f.GetField('row_id'), f.GetField('u_1_2'), f.GetField('quality'))
            for f in layer]
    data = None
    top_row = max(row for _, row, _, _ in rows)

    features = []
    for col, row, u, quality in rows:
        left, bottom = col*CELL_W, row*CELL_H
        right, top = left+CELL_W, bottom+CELL_H
        # The rectangle's corners must match left/bottom/right/top exactly (ScenarioGrid checks this).
        ring = [[left, top], [right, top], [right, bottom], [left, bottom], [left, top]]
        features.append({'type': 'Feature',
                         'geometry': {'type': 'Polygon', 'coordinates': [ring]},
                         'properties': {'col_index': col,
                                        'row_index': top_row-row,   # north -> south, as ScenarioGrid expects
                                        'left': left, 'bottom': bottom, 'right': right, 'top': top,
                                        'u_1_2': u, 'quality': quality}})

    target = Path(target)
    partial = target.with_name(target.stem + '.partial.geojson')
    partial.write_text(json.dumps({'type': 'FeatureCollection',
                                   'crs': {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:EPSG::28992'}},
                                   'features': features}))
    partial.replace(target)
    print(f'{len(features)} cells -> {target}')


if __name__ == '__main__':
    if len(sys.argv) not in (1, 3):
        sys.exit(__doc__)
    source, target = (sys.argv[1], sys.argv[2]) if len(sys.argv) == 3 else (area.WIND, area.WIND_GRID)
    main(str(source), str(target))
