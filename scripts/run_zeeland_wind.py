"""Resumable Zeeland batch around the unchanged wind service; bounded scratch storage."""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/zeeland-wind'
PACKAGE=Path('/inputs')
OFFICIAL_INDEX = Path(os.environ.get('ZEELAND_AHN_INDEX', str(OUT / 'official-bladwijzer.gpkg')))
SCRATCH=Path(os.environ.get('ZEELAND_SCRATCH',str(OUT/'_scratch')))


def atomic_json(path,value):
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8')
    temporary.replace(path)


def polygons_only(geometry):
    from osgeo import ogr
    result=ogr.Geometry(ogr.wkbMultiPolygon)
    def visit(part):
        if part is None:return
        if part.GetGeometryName()=='POLYGON':
            copy=part.Clone();copy.FlattenTo2D();result.AddGeometry(copy)
        elif part.GetGeometryName() in ('MULTIPOLYGON','GEOMETRYCOLLECTION'):
            for child in part:visit(child)
    visit(geometry)
    return result


def safe_clear(path):
    target=path.resolve();base=SCRATCH.resolve()
    if target==base or base not in target.parents:
        raise ValueError('Refusing cleanup outside per-tile batch scratch: '+str(target))
    if target.exists():shutil.rmtree(target)


def prepare_index():
    from osgeo import ogr
    if not OFFICIAL_INDEX.is_file():
        raise FileNotFoundError(f'Provide the official AHN tile index at {OFFICIAL_INDEX}')
    source=ogr.Open(str(PACKAGE/'02_inputs/zeeland/Zeeland_research_layers.gpkg'))
    official=ogr.Open(str(OFFICIAL_INDEX))
    current={f.GetField('AHN'):f.items() for f in official.GetLayer(0)}
    destination=OUT/'tile-index.gpkg'
    if not destination.exists():
        copy=ogr.GetDriverByName('GPKG').CreateDataSource(str(destination))
        layer=copy.CopyLayer(source.GetLayerByName('ahn5_land_tiles'),'ahn5_land_tiles')
        layer.StartTransaction()
        for f in layer:
            new=current.get(f.GetField('AHN'),{})
            for name,field in [('dtm_url','AHN5_05M_M'),('dsm_url','AHN5_05M_R')]:
                if new.get(field):f.SetField(name,new[field])
            url=f.GetField('dsm_url')
            if url:
                year=re.search(r'/(20\d\d)_',url)
                if year:f.SetField('acquisition_year',year.group(1))
            layer.SetFeature(f)
        layer.CommitTransaction();copy=None
    index=ogr.Open(str(destination));layer=index.GetLayerByName('ahn5_land_tiles')
    tiles=[]
    for f in layer:
        item=f.items();item['geometry_wkt']=f.GetGeometryRef().ExportToWkt()
        box=f.GetGeometryRef().GetEnvelope();item['bounds']=[box[0],box[2],box[1],box[3]]
        assert box[0]%250==0 and box[3]%125==0
        assert box[1]-box[0]==5000 and box[3]-box[2]==6250
        tiles.append(item)
    return sorted(tiles,key=lambda t:(-t['land_overlap_km2'],t['AHN']))


def work(tile,events):
    code=tile['AHN'];saved=OUT/'tiles'/code
    if (saved/'completed.json').exists():return code,'complete',None
    events.put(('running',code))
    scratch=SCRATCH/code
    scratch.mkdir(parents=True,exist_ok=True);saved.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,str(ROOT/'scripts/run_neighbour_wind.py'),'--tile',code,
        '--output',str(scratch),'--grid-width','250','--grid-height','125','--allow-empty-features',
        '--tile-index',str(OUT/'tile-index.gpkg')]
    try:
        if shutil.disk_usage(OUT).free < 6*1024**3:
            raise RuntimeError('Less than 6 GiB free; batch scratch limit reached')
        with (saved/'run.log').open('w',encoding='utf-8') as log:
            completed=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=1800)
        if completed.returncode:
            raise RuntimeError('Pipeline failed; see tiles/'+code+'/run.log')
        record=json.loads((scratch/'completed-run.json').read_text())
        run=Path(record['wind_grid']).parent
        for name in ['wind-grid.gpkg','wind-grid.geojson','run-settings.json']:
            shutil.copy2(run/name,saved/name)
        shutil.copy2(scratch/'input-provenance.json',saved/'input-provenance.json')
        # Keep selected vector snapshots; large immutable height sources can be redownloaded by URL/hash.
        with zipfile.ZipFile(saved/'selected-inputs.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for name in ['buildings.gpkg','trees.gpkg']:
                archive.write(scratch/name,name)
        atomic_json(saved/'completed.json',dict(status='complete',tile=code,
            generation_seconds=record['generation_seconds'],finished_utc=datetime.now(timezone.utc).isoformat(),
            source_inputs_retained='Selected feature GeoPackages archived; AHN URLs and hashes retained; height scratch removed.'))
        safe_clear(scratch)
        return code,'complete',None
    except Exception as error:
        atomic_json(saved/'failure.json',dict(status='failed',tile=code,error=str(error)))
        # Preserve compact download/provenance evidence, release only this task's scratch.
        if (scratch/'input-provenance.json').exists():shutil.copy2(scratch/'input-provenance.json',saved/'input-provenance.json')
        safe_clear(scratch)
        return code,'failed',str(error)


def seed_known():
    known={
        '65DN1':ROOT/'output/aligned-wind-grid/65DN1',
        '65DN2':ROOT/'output/aligned-wind-grid/65DN2',
    }
    for code in ['65BZ1','65BZ2']:
        directory=ROOT/'output/four-tile-wind'/code
        manifest = directory / 'completed-run.json'
        if not manifest.is_file():
            continue
        record=json.loads(manifest.read_text())
        known[code]=directory/'runs'/Path(record['geojson']).parent.name
    for code,source in known.items():
        saved=OUT/'tiles'/code;saved.mkdir(parents=True,exist_ok=True)
        if (saved/'completed.json').exists():continue
        if not all((source / name).is_file() for name in ('wind-grid.gpkg', 'wind-grid.geojson')):
            continue
        for name in ['wind-grid.gpkg','wind-grid.geojson']:
            shutil.copy2(source/name,saved/name)
        atomic_json(saved/'completed.json',dict(status='complete',tile=code,reused_source=str(source),
            finished_utc=datetime.now(timezone.utc).isoformat(),source_inputs_retained='Original saved experiment folders retained.'))


def assemble(tiles,state):
    from osgeo import ogr,osr
    path=OUT/'zeeland-wind.gpkg'
    temporary=OUT/'zeeland-wind-next.gpkg'
    if temporary.exists():temporary.unlink()
    package=ogr.Open(str(PACKAGE/'02_inputs/zeeland/Zeeland_research_layers.gpkg'))
    boundary_feature=package.GetLayerByName('province_boundary').GetNextFeature()
    boundary=boundary_feature.GetGeometryRef().Clone()
    if not boundary.IsValid():boundary=boundary.MakeValid()
    land_layer=package.GetLayerByName('land_mask_candidate')
    rd=osr.SpatialReference();rd.ImportFromEPSG(28992)
    destination=ogr.GetDriverByName('GPKG').CreateDataSource(str(temporary))
    layer=destination.CreateLayer('wind_zeeland_land_candidate',rd,ogr.wkbMultiPolygon)
    fields=[('tile',ogr.OFTString),('column_id',ogr.OFTInteger),('row_id',ogr.OFTInteger),
        ('u_1_2',ogr.OFTReal),('display_u',ogr.OFTReal),('quality',ogr.OFTString),('land_m2',ogr.OFTReal)]
    for name,kind in fields:layer.CreateField(ogr.FieldDefn(name,kind))
    coverage=destination.CreateLayer('tile_coverage',rd,ogr.wkbPolygon)
    for name,kind in [('tile',ogr.OFTString),('status',ogr.OFTString),('land_km2',ogr.OFTReal)]:coverage.CreateField(ogr.FieldDefn(name,kind))
    counts=dict(cells=0,positive=0,legacy_missing_zero=0,land_area_m2=0,expected_land_area_m2=0)
    for tile in tiles:
        code=tile['AHN'];status=state['tiles'][code]['status']
        tile_geometry=ogr.CreateGeometryFromWkt(tile['geometry_wkt'])
        record=ogr.Feature(coverage.GetLayerDefn());record.SetGeometry(tile_geometry)
        record.SetField('tile',code);record.SetField('status',status);record.SetField('land_km2',tile['land_overlap_km2']);coverage.CreateFeature(record)
        if status!='complete':continue
        tile_boundary=boundary.Intersection(tile_geometry)
        pieces=ogr.Geometry(ogr.wkbMultiPolygon)
        land_layer.SetSpatialFilter(tile_geometry)
        for part in land_layer:
            geometry=polygons_only(part.GetGeometryRef().Intersection(tile_boundary))
            if geometry.IsEmpty() or geometry.GetArea()<=0:continue
            if not geometry.IsValid():geometry=geometry.MakeValid()
            geometry=polygons_only(geometry)
            for polygon in geometry:
                if polygon.GetGeometryType() in (ogr.wkbPolygon,ogr.wkbPolygon25D):pieces.AddGeometry(polygon)
        tile_land=pieces.UnionCascaded() if pieces.GetGeometryCount() else ogr.Geometry(ogr.wkbMultiPolygon)
        if tile_land.IsEmpty():continue
        counts['expected_land_area_m2']+=tile_land.GetArea()
        source=ogr.Open(str(OUT/'tiles'/code/'wind-grid.gpkg'));original=source.GetLayer(0)
        if original.GetFeatureCount()!=1000:raise ValueError('Unexpected grid count in '+code)
        layer.StartTransaction()
        for f in original:
            geometry=f.GetGeometryRef();xmin,xmax,ymin,ymax=geometry.GetEnvelope()
            if xmax-xmin!=250 or ymax-ymin!=125 or xmin%250 or ymin%125:raise ValueError('Grid misalignment: '+code)
            if not geometry.Intersects(tile_land):continue
            clipped=polygons_only(geometry.Intersection(tile_land))
            if clipped.IsEmpty() or clipped.GetArea()<=1e-6:continue
            value=f.GetField('u_1_2')
            if value is None or not math.isfinite(value) or value<0:raise ValueError('Invalid wind value in '+code)
            missing=value==0 and (f.GetField('lambda_total') is None or f.GetField('u_h') is None)
            record=ogr.Feature(layer.GetLayerDefn());record.SetGeometry(clipped)
            for name,v in [('tile',code),('column_id',int(xmin/250)),('row_id',int(ymin/125)),
                ('u_1_2',value),('quality','missing_calculation' if missing else 'model_value'),('land_m2',clipped.GetArea())]:record.SetField(name,v)
            if not missing:record.SetField('display_u',value)
            if layer.CreateFeature(record)!=0:raise ValueError('Failed to export cell in '+code)
            counts['cells']+=1;counts['positive']+=value>0;counts['legacy_missing_zero']+=missing;counts['land_area_m2']+=clipped.GetArea()
        layer.CommitTransaction()
    destination=None
    if not math.isclose(counts['land_area_m2'],counts['expected_land_area_m2'],rel_tol=1e-8,abs_tol=1):
        raise ValueError('Assembled cell area does not cover the completed-tile candidate land area')
    temporary.replace(path)
    return counts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers',type=int,choices=[1,2],default=2)
    parser.add_argument('--max-new-tiles',type=int)
    parser.add_argument('--assemble-only',action='store_true')
    parser.add_argument('--skip-assembly',action='store_true')
    parser.add_argument('--skip-failed',action='store_true')
    args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    tiles=prepare_index();seed_known()
    state=dict(started_utc=datetime.now(timezone.utc).isoformat(),grid_width=250,grid_height=125,wind_from='west',
        application='Unchanged main WindService',workers=args.workers,
        source_index_sha256=hashlib.sha256(OFFICIAL_INDEX.read_bytes()).hexdigest(),tiles={})
    for tile in tiles:
        code=tile['AHN']
        status='complete' if (OUT/'tiles'/code/'completed.json').exists() else 'unavailable' if not tile['dsm_url'] or not tile['dtm_url'] else 'failed' if args.skip_failed and (OUT/'tiles'/code/'failure.json').exists() else 'pending'
        state['tiles'][code]=dict(status=status,land_km2=tile['land_overlap_km2'])
    def save():
        state['counts']={status:sum(t['status']==status for t in state['tiles'].values()) for status in ['pending','running','complete','failed','unavailable']}
        state['updated_utc']=datetime.now(timezone.utc).isoformat()
        atomic_json(OUT/'batch-state.json',state)
        print('Progress',state['counts'],flush=True)
    save()
    pending=[t for t in tiles if state['tiles'][t['AHN']]['status']=='pending']
    if args.max_new_tiles is not None:pending=pending[:args.max_new_tiles]
    if not args.assemble_only:
        events=queue.Queue()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures={}
            for tile in pending:
                # At most two child processes; generated files live in isolated tile folders.
                futures[pool.submit(work,tile,events)]=tile['AHN']
            while futures:
                done,_=concurrent.futures.wait(futures,timeout=5,return_when=concurrent.futures.FIRST_COMPLETED)
                changed=False
                while not events.empty():
                    status,code=events.get_nowait();state['tiles'][code]['status']=status;changed=True
                for future in done:
                    code,status,error=future.result()
                    state['tiles'][code]['status']=status
                    if error:state['tiles'][code]['error']=error
                    del futures[future];changed=True
                if changed:save()
    if args.skip_assembly and any(t['status']=='pending' for t in state['tiles'].values()):
        state['status']='processing_checkpoint';save();return
    state['assembled']=assemble(tiles,state)
    state['status']='complete' if all(t['status']=='complete' for t in state['tiles'].values()) else 'partial_with_explicit_gaps'
    save()
    print('Map saved:',OUT/'zeeland-wind.gpkg',flush=True)


if __name__=='__main__':main()
