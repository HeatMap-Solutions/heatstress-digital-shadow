"""Fetch an adjoining AHN tile and run the unchanged WindService with local inputs."""
import hashlib
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/neighbour-wind'
PACKAGE=Path('/inputs')
DOWNLOAD_ADAPTATIONS=[]


def download(url,path):
    if path.is_file() and path.stat().st_size:
        return
    temporary=path.with_suffix(path.suffix+'.part')
    for attempt in range(8):
        try:
            with urllib.request.urlopen(url,timeout=90) as response,temporary.open('wb') as stream:
                while True:
                    block=response.read(1024*1024)
                    if not block: break
                    stream.write(block)
            temporary.replace(path)
            print('Downloaded',path.name,path.stat().st_size,flush=True)
            return
        except Exception as error:
            # Same cursor, fewer records per response: preserve complete coverage.
            parts=urllib.parse.urlsplit(url)
            params=dict(urllib.parse.parse_qsl(parts.query))
            limit=int(params.get('limit','0'))
            if getattr(error,'code',None)==500 and limit>100:
                previous=url;params['limit']=str(max(100,limit//2))
                url=urllib.parse.urlunsplit((parts.scheme,parts.netloc,parts.path,urllib.parse.urlencode(params),parts.fragment))
                DOWNLOAD_ADAPTATIONS.append(dict(original_url=previous,retry_url=url,reason='HTTP 500; smaller page at identical cursor'))
                print('Retrying same cursor with page limit',params['limit'],flush=True)
            if attempt==7: raise
            time.sleep(min(30,2**attempt))


def main():
    global OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tile',default='65DN2')
    parser.add_argument('--output',type=Path,default=OUT)
    parser.add_argument('--grid-width',type=float,default=250)
    parser.add_argument('--grid-height',type=float,default=125)
    parser.add_argument('--allow-empty-features',action='store_true')
    parser.add_argument('--tile-index',type=Path)
    args=parser.parse_args()
    OUT=args.output.resolve()
    from osgeo import gdal,ogr,osr
    OUT.mkdir(parents=True,exist_ok=True)
    index=ogr.Open(str(args.tile_index or PACKAGE/'02_inputs/zeeland/Zeeland_research_layers.gpkg'))
    layer=index.GetLayerByName('ahn5_land_tiles')
    choices=[]
    for f in layer:
        bounds=f.GetGeometryRef().GetEnvelope()
        if f.GetField('AHN')==args.tile:
            choices.append((f.items(),bounds))
    if len(choices)!=1:
        raise ValueError(f'Expected one index tile {args.tile}, found {len(choices)}')
    tile,bounds=choices[0]
    code=tile['AHN']; extent=(bounds[0],bounds[2],bounds[1],bounds[3])
    print('Selected tile:',code,extent,flush=True)
    sources={}
    for kind,source_key in [('dsm','dsm_url'),('dtm','dtm_url')]:
        raw=OUT/(kind+'-raw.tif')
        download(tile[source_key],raw)
        ds=gdal.Open(str(raw))
        if ds is None: raise ValueError('Invalid downloaded '+kind)
        t=ds.GetGeoTransform()
        actual=(t[0],t[3]+ds.RasterYSize*t[5],t[0]+ds.RasterXSize*t[1],t[3])
        if any(abs(a-b)>.01 for a,b in zip(actual,extent)):
            raise ValueError(f'{kind} extent differs from selected index tile: {actual}')
        prepared=OUT/(kind+'-1m.tif')
        if not prepared.exists():
            result=gdal.Warp(str(prepared),ds,xRes=1,yRes=1,resampleAlg='average',
                outputType=gdal.GDT_Float32,creationOptions=['TILED=YES','COMPRESS=DEFLATE'])
            if result is None: raise ValueError('Failed to prepare '+kind)
            result=None
        sources[kind]=dict(url=tile[source_key],raw_bytes=raw.stat().st_size,
            raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),prepared=str(prepared),
            prepared_sha256=hashlib.sha256(prepared.read_bytes()).hexdigest(),resampling='average to 1 m before unchanged pipeline')

    rd=osr.SpatialReference(); rd.ImportFromEPSG(28992); rd.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    wgs=osr.SpatialReference(); wgs.ImportFromEPSG(4326); wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_wgs=osr.CoordinateTransformation(rd,wgs); to_rd=osr.CoordinateTransformation(wgs,rd)
    # Densify all four buffered edges for a conservative CRS84 request rectangle.
    xmin,ymin,xmax,ymax=extent
    points=[]
    for i in range(22):
        fraction=i/21
        points += [(xmin-10+(xmax-xmin+20)*fraction,ymin-10),(xmin-10+(xmax-xmin+20)*fraction,ymax+10),
                   (xmin-10,ymin-10+(ymax-ymin+20)*fraction),(xmax+10,ymin-10+(ymax-ymin+20)*fraction)]
    ll=[to_wgs.TransformPoint(x,y) for x,y in points]
    bbox=[min(p[0] for p in ll),min(p[1] for p in ll),max(p[0] for p in ll),max(p[1] for p in ll)]
    definitions=[('buildings','https://api.pdok.nl/kadaster/bag/ogc/v2/collections/pand/items'),
                 ('trees','https://api.pdok.nl/kadaster/bgt/ogc/v1/collections/vegetatieobject_punt/items')]
    provenance=[]
    prepared_features={}
    for name,url in definitions:
        folder=OUT/('pages-'+name); folder.mkdir(exist_ok=True)
        params=dict(f='json',bbox=','.join(map(str,bbox)),limit=1000)
        if name=='trees':params.update({'filter-lang':'cql2-text','filter':'eind_registratie IS NULL'})
        next_url=url+'?'+urllib.parse.urlencode(params); first=next_url; seen=set(); records=[]; page=0
        while next_url:
            if next_url in seen or page>=500:raise ValueError('Pagination did not terminate')
            seen.add(next_url); page+=1
            target=folder/f'{page:04d}.json'
            download(next_url,target)
            data=json.loads(target.read_text())
            if data.get('type')!='FeatureCollection':raise ValueError('Not a FeatureCollection')
            records.extend(data['features'])
            next_url=next((urllib.parse.urljoin(next_url,l['href']) for l in data.get('links',[]) if l.get('rel')=='next'),None)
            print(name,'page',page,'records',len(records),flush=True)
        key='identificatie' if name=='buildings' else 'lokaal_id'
        unique={}
        order='documentdatum' if name=='buildings' else 'tijdstip_registratie'
        for f in records:
            p=f['properties']; identifier=str(p.get(key) or f.get('id'))
            if identifier not in unique or str(p.get(order) or '')>=str(unique[identifier]['properties'].get(order) or ''):
                unique[identifier]=f
        rectangle=ogr.CreateGeometryFromWkt(f'POLYGON (({xmin} {ymin},{xmax} {ymin},{xmax} {ymax},{xmin} {ymax},{xmin} {ymin}))')
        destination=OUT/(name+'.gpkg')
        if destination.exists():
            destination.unlink()  # Exact generated file in this task's output directory only.
        target=ogr.GetDriverByName('GPKG').CreateDataSource(str(destination))
        output=target.CreateLayer(name,rd,ogr.wkbMultiPolygon if name=='buildings' else ogr.wkbPoint)
        output.CreateField(ogr.FieldDefn('source_id',ogr.OFTString))
        statuses={'Pand in gebruik','Pand in gebruik (niet ingemeten)','Pand buiten gebruik','Verbouwing pand','Sloopvergunning verleend'}
        selected=0
        output.StartTransaction()
        for identifier,f in unique.items():
            p=f['properties']
            if name=='buildings' and p.get('status') not in statuses:continue
            if name=='trees' and (p.get('eind_registratie') is not None or p.get('termination_date') is not None or p.get('status')!='bestaand' or p.get('plus_type')!='boom'):continue
            if f.get('geometry') is None:continue
            geom=ogr.CreateGeometryFromJson(json.dumps(f['geometry'])); geom.Transform(to_rd)
            if not geom.Intersects(rectangle):continue
            if name=='buildings':
                if not geom.IsValid():geom=geom.MakeValid()
                geom=geom.Intersection(rectangle)
                if geom.IsEmpty():continue
                geom=ogr.ForceToMultiPolygon(geom)
            record=ogr.Feature(output.GetLayerDefn());record.SetField('source_id',identifier);record.SetGeometry(geom)
            if output.CreateFeature(record)!=0:raise ValueError('Could not write feature')
            selected+=1
        output.CommitTransaction();target=None
        if selected==0 and not args.allow_empty_features:raise ValueError('No selected '+name+' features')
        prepared_features[name]=destination
        provenance.append(dict(collection=name,source_url=url,first_request=first,pages=page,
            received=len(records),unique_identifiers=len(unique),selected=selected,complete=True,
            selected_sha256=hashlib.sha256(destination.read_bytes()).hexdigest()))
        print('Selected',name,selected,flush=True)
    manifest=dict(tile=code,extent_rd=extent,acquisition_year=tile['acquisition_year'],
        prepared_at_utc=datetime.now(timezone.utc).isoformat(),height_sources=sources,features=provenance,download_adaptations=DOWNLOAD_ADAPTATIONS,
        settings=dict(wind_direction='west',grid_width=args.grid_width,grid_height=args.grid_height),
        preparation='AHN5 average-resampled 1 m; same BAG existing-status selection and BGT current tree selection as supplied pilot.',
        limitations=['PDOK snapshot is newer than the 26 October Middelburg feature snapshot.',
                    'No neighbouring reference wind map or field observations supplied.',
                    'No halo or grid-origin adjustment is added to the unchanged pipeline.'])
    (OUT/'input-provenance.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    sys.path.insert(0,str(ROOT/'qgis'))
    from src.configs.preflight import init_qgis
    app=init_qgis()
    from src.services.wind.wind_service import WindService
    from src.utils.layer_utils import load_vector_layer,export_layer_to_geopackage
    class SavedSources:
        def export(self,name,destination,extent):
            source=load_vector_layer(str(prepared_features[name]),name)
            path=export_layer_to_geopackage(source,destination,extent)
            return dict(path=path,layer=load_vector_layer(path,name))
        def import_buildings(self,output_geopackage_path,extent):return self.export('buildings',output_geopackage_path,extent)
        def import_trees(self,output_geopackage_path,extent):return self.export('trees',output_geopackage_path,extent)
    service=WindService();service.wfs=SavedSources()
    started=time.perf_counter()
    result=service.generate_wind_reduction_map(dsm_path=sources['dsm']['prepared'],dtm_path=sources['dtm']['prepared'],
        output_dir=str(OUT/'runs'),wind_direction='west',grid_cell_width=args.grid_width,grid_cell_height=args.grid_height)
    generation_seconds=time.perf_counter()-started
    path=Path(result['outputs']['wind_grid']); ds=ogr.Open(str(path)); layer=ds.GetLayer(0)
    export=ogr.GetDriverByName('GeoJSON').CreateDataSource(str(path.with_suffix('.geojson')))
    export.CopyLayer(layer,'wind_grid');export=None
    (OUT/'completed-run.json').write_text(json.dumps(dict(status=result['status'],tile=code,
        wind_grid=str(path),geojson=str(path.with_suffix('.geojson')),inputs=manifest,generation_seconds=generation_seconds),indent=2),encoding='utf-8')
    print('Completed:',path,flush=True)


if __name__=='__main__':main()
