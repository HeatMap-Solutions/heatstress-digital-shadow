from abc import ABC
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


class LegendController(ABC):
    """Read the selected layer's actual published style without importing QGIS."""

    def __init__(self, data_root='/data'):
        self.root = Path(data_root)

    def get_legend(self, layer='pet-version-1'):
        if layer == 'pet-session-updated':
            layer = 'pet-version-1'  # The session layer inherits this renderer.
        with zipfile.ZipFile(self.root/'server/starting-map.qgz') as archive:
            name = next(name for name in archive.namelist() if name.endswith('.qgs'))
            project = ET.fromstring(archive.read(name))
        map_layer = next((item for item in project.findall('./projectlayers/maplayer')
                          if item.findtext('layername') == layer), None)
        if map_layer is None:
            raise ValueError(f'Unknown map layer: {layer}')
        renderer = map_layer.find('.//rasterrenderer')
        shader = map_layer.find('.//colorrampshader')
        if renderer is not None and shader is not None:
            return {
                'renderer': {'type': renderer.get('type'), 'band': int(renderer.get('band', 1)),
                             'classification_min': float(renderer.get('classificationMin')),
                             'classification_max': float(renderer.get('classificationMax')),
                             'opacity': float(renderer.get('opacity', 1))},
                'color_ramp': {'type': shader.get('colorRampType'),
                               'mode': shader.get('classificationMode'), 'clip': shader.get('clip'),
                               'items': [{'value': float(item.get('value')), 'label': item.get('label'),
                                          'color': item.get('color')} for item in shader.findall('item')]}}
        renderer = map_layer.find('renderer-v2')
        if renderer is None or renderer.get('type') != 'graduatedSymbol':
            raise ValueError(f'Unsupported legend renderer: {layer}')
        colors = {}
        for symbol in renderer.findall('./symbols/symbol'):
            color = symbol.find('.//Option[@name="color"]')
            colors[symbol.get('name')] = ','.join(color.get('value').split(',')[:4])
        ranges = renderer.findall('./ranges/range')
        return {
            'renderer': {'type': 'graduatedSymbol', 'band': 0,
                         'classification_min': min(float(item.get('lower')) for item in ranges),
                         'classification_max': max(float(item.get('upper')) for item in ranges),
                         'opacity': 1},
            'color_ramp': {'type': 'discrete', 'mode': 'discrete', 'clip': None,
                           'items': [{'value': float(item.get('lower')), 'label': item.get('label'),
                                      'color': colors[item.get('symbol')]} for item in ranges]}}
