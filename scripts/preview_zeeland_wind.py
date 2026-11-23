"""Save a portable QGIS project and labelled preview of the assembled wind export."""
import json
import sys
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/zeeland-wind'


def main():
    sys.path.insert(0,str(ROOT/'qgis'))
    from src.configs.preflight import init_qgis
    app=init_qgis()
    from qgis.core import (QgsProject,QgsVectorLayer,QgsFillSymbol,QgsRendererRange,
        QgsGraduatedSymbolRenderer,QgsSingleSymbolRenderer,QgsMapSettings,QgsMapRendererParallelJob,QgsRectangle)
    from qgis.PyQt.QtGui import QColor,QPainter,QFont,QImage
    from qgis.PyQt.QtCore import QSize,Qt
    state=json.loads((OUT/'batch-state.json').read_text())
    local=tempfile.TemporaryDirectory(prefix='wind_preview_')
    path=Path(local.name)/'zeeland-wind.gpkg'
    shutil.copy2(OUT/'zeeland-wind.gpkg',path)
    values=QgsVectorLayer(str(path)+'|layername=wind_zeeland_land_candidate','Wind model values — west','ogr')
    missing=QgsVectorLayer(str(path)+'|layername=wind_zeeland_land_candidate','Missing calculations (legacy zeros)','ogr')
    gaps=QgsVectorLayer(str(path)+'|layername=tile_coverage','Unfinished or unavailable tile inputs','ogr')
    assert all(l.isValid() for l in [values,missing,gaps])
    snapshot_tiles=sum(f['status']=='complete' for f in gaps.getFeatures())
    values.setSubsetString("quality = 'model_value'")
    missing.setSubsetString("quality = 'missing_calculation'")
    gaps.setSubsetString("status <> 'complete'")
    assert values.featureCount()+missing.featureCount()>0,'No wind cells could be read for preview'
    maximum=max([f['display_u'] for f in values.getFeatures()] or [1])
    upper=max(1,maximum)
    breaks=[0,.2,.4,.6,.8,upper]
    colors=['#440154','#3b528b','#21918c','#5ec962','#fde725']
    ranges=[]
    for low,high,color in zip(breaks[:-1],breaks[1:],colors):
        symbol=QgsFillSymbol.createSimple({'color':color,'outline_style':'no'})
        ranges.append(QgsRendererRange(low,high,symbol,f'{low:.1f}–{high:.1f}'))
    values.setRenderer(QgsGraduatedSymbolRenderer('display_u',ranges))
    missing.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({'color':'#d3d3d3','outline_style':'no'})))
    gaps.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({'color':'255,230,220,80','outline_color':'#c76d4d','outline_width':'.2'})))
    project=QgsProject.instance();project.clear()
    for layer in [gaps,missing,values]:project.addMapLayer(layer)
    project.setCrs(values.crs())
    extent=QgsRectangle(10425,357829,77734,422664);extent.scale(1.06)
    project.viewSettings().setDefaultViewExtent(__import__('qgis.core',fromlist=['QgsReferencedRectangle']).QgsReferencedRectangle(extent,values.crs()))
    project.setTitle('Zeeland wind — west wind, unchanged pipeline')
    project.setFileName(str(OUT/'zeeland-wind.qgs'))
    if not project.write():raise RuntimeError('Could not save QGIS project')
    tree=ET.parse(OUT/'zeeland-wind.qgs')
    for datasource in tree.findall('.//maplayer/datasource'):
        name=datasource.text.split('|layername=',1)[1]
        datasource.text='./zeeland-wind.gpkg|layername='+name
    for element in tree.iter():
        source=element.get('source')
        if source and 'zeeland-wind.gpkg|layername=' in source:
            element.set('source','./zeeland-wind.gpkg|layername='+source.split('|layername=',1)[1])
    tree.write(OUT/'zeeland-wind.qgs',encoding='utf-8',xml_declaration=True)
    settings=QgsMapSettings();settings.setLayers([values,missing,gaps]);settings.setDestinationCrs(values.crs())
    settings.setExtent(extent);settings.setOutputSize(QSize(1400,1400));settings.setBackgroundColor(QColor('white'))
    job=QgsMapRendererParallelJob(settings);job.start();job.waitForFinished()
    map_image=job.renderedImage()
    image=QImage(1400,1650,QImage.Format.Format_ARGB32);image.fill(QColor('white'))
    painter=QPainter(image)
    painter.drawImage(0,100,map_image)
    painter.fillRect(0,0,1400,95,QColor('white'))
    painter.setPen(QColor('#18262e'));font=QFont('DejaVu Sans',22);font.setBold(True);painter.setFont(font)
    painter.drawText(35,43,'Zeeland wind map — west wind')
    painter.setFont(QFont('DejaVu Sans',12))
    painter.drawText(35,75,f"Map snapshot: {snapshot_tiles} / {len(state['tiles'])} tiles | 250 × 125 m cells | Raw model values")
    painter.fillRect(0,1500,1400,150,QColor('white'))
    x=35
    for low,high,color in zip(breaks[:-1],breaks[1:],colors):
        painter.fillRect(x,1520,25,20,QColor(color));painter.setPen(QColor('#18262e'))
        painter.drawText(x+32,1536,f'{low:.1f}–{high:.1f}');x+=155
    painter.fillRect(x,1520,25,20,QColor('#d3d3d3'));painter.drawText(x+32,1536,'Missing calculation')
    painter.setFont(QFont('DejaVu Sans',10))
    painter.drawText(35,1580,'Candidate land mask; grey areas preserve legacy zeros caused by NULL calculations.')
    painter.drawText(35,1611,'Fixed-grid model output, not observed wind or validated physical accuracy. Tile gaps are recorded separately.')
    painter.end()
    if not image.save(str(OUT/'zeeland-wind-preview.png')):raise RuntimeError('Could not save preview')
    print(OUT/'zeeland-wind.qgs');print(OUT/'zeeland-wind-preview.png')


if __name__=='__main__':main()
