"""Structural and coverage checks for a complete or partial Zeeland wind export."""
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/zeeland-wind'


def main():
    from osgeo import ogr
    state=json.loads((OUT/'batch-state.json').read_text())
    path=OUT/'zeeland-wind.gpkg'
    dataset=ogr.Open(str(path));layer=dataset.GetLayerByName('wind_zeeland_land_candidate')
    assert layer.GetSpatialRef().GetAuthorityCode(None)=='28992'
    counts=Counter();invalid=[];area=0
    for f in layer:
        tile=f.GetField('tile');value=f.GetField('u_1_2');display=f.GetField('display_u');quality=f.GetField('quality')
        assert state['tiles'][tile]['status']=='complete'
        assert value is not None and math.isfinite(value) and value>=0
        assert (display is None)==(quality=='missing_calculation')
        if display is not None:assert display==value
        geometry=f.GetGeometryRef()
        assert geometry is not None and not geometry.IsEmpty()
        assert geometry.GetGeometryName()=='MULTIPOLYGON'
        if not geometry.IsValid():invalid.append(f.GetFID())
        xmin,xmax,ymin,ymax=geometry.GetEnvelope()
        x=f.GetField('column_id')*250;y=f.GetField('row_id')*125
        assert xmin>=x-1e-5 and xmax<=x+250+1e-5 and ymin>=y-1e-5 and ymax<=y+125+1e-5
        area+=geometry.GetArea();counts[quality]+=1
    assert not invalid,invalid[:10]
    connection=sqlite3.connect(path)
    duplicates=connection.execute('SELECT column_id,row_id,COUNT(*) FROM wind_zeeland_land_candidate GROUP BY column_id,row_id HAVING COUNT(*)>1 LIMIT 10').fetchall()
    assert not duplicates,duplicates
    assert counts.total()==state['assembled']['cells']
    assert math.isclose(area,state['assembled']['land_area_m2'],rel_tol=1e-8,abs_tol=1)
    completed=[code for code,t in state['tiles'].items() if t['status']=='complete']
    missing=[dict(tile=code,**t) for code,t in state['tiles'].items() if t['status']!='complete']
    result=dict(structural_checks='passed',province_complete=not missing,
        completed_tiles=len(completed),inventory_tiles=len(state['tiles']),
        quality_counts=dict(counts),candidate_land_coverage_km2=area/1e6,
        duplicate_global_cells=0,invalid_geometries=0,unfinished_or_unavailable=missing,
        interpretation='Candidate-land export; structural correctness does not establish physical wind accuracy.')
    (OUT/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    report=['# Zeeland wind map processing record','',
        'The province-wide batch uses the unchanged main WindService, wind from west, and a shared '
        '250 m wide × 125 m tall RD grid. Only local source imports and batch orchestration are external '
        'to the application. The four tested tiles are reused; other tiles use the recorded AHN5 links '
        'and current BAG/BGT feature selections.','',
        f"Completed tiles: {len(completed)} of {len(state['tiles'])}. Candidate-land output coverage: {area/1e6:.6f} km². "
        f"Output cells: {counts.total()}; model-value cells: {counts['model_value']}; missing-calculation cells: {counts['missing_calculation']}.", '',
        '## How to use the map','',
        '- Open zeeland-wind.gpkg and select wind_zeeland_land_candidate.',
        '- Use display_u for a map that leaves missing calculations empty; original u_1_2 values are preserved.',
        '- Use quality to distinguish model values from inherited NULL-to-zero results.',
        '- tile_coverage records every inventory tile, including pending, failed and unavailable inputs.',
        '- The cell geometries are clipped to the supplied candidate land mask and province boundary. '
        'That mask is not an independently validated coastline. Raw full-cell outputs are retained per tile.','',
        '## Structural verification','',
        'The export uses EPSG:28992, has unique global cell IDs, valid clipped geometries, finite nonnegative '
        'raw values, and consistent missing-value display fields. The export area matches completed-tile '
        'candidate land, without silently declaring unfinished tiles covered.','',
        '## Remaining tiles','',
        '| Tile | Status | Candidate land km² |','| --- | --- | ---: |',
        *[f"| {t['tile']} | {t['status']} | {t['land_km2']:.9f} |" for t in missing], '',
        '## Provenance and limits','',
        '- official-bladwijzer.gpkg is the current official AHN index; tile-index.gpkg preserves the selected Zeeland inventory with refreshed links.',
        '- Each tile folder retains its final map, processing log, settings and input hashes; newly gathered vector snapshots are archived.',
        '- Immutable AHN source URLs and hashes are retained. Large temporary height/intermediate files are removed only from the batch scratch folder.',
        '- Two workers process tiles concurrently; generation timings are operational observations, not controlled benchmark averages.',
        '- Feature snapshots are mixed in date because completed experiments were reused.',
        '- The known NULL-to-zero behaviour and fixed-grid edge effects remain unchanged.',
        '- West wind is one scenario, not all wind directions or a live weather map.',
        '- Do not interpret successful export, province coverage or model agreement as measured physical accuracy.','',
        'Resume: run scripts/run_zeeland_wind.py in the QGIS container with /inputs mounted read-only. '
        'Completed tiles are skipped, failed tiles are retried on the next run, and coverage state is saved.']
    (OUT/'zeeland-wind-report.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='unfinished_or_unavailable'},indent=2))


if __name__=='__main__':main()
