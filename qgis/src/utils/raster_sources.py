"""Resolve elevation and move virtual raster descriptors without changing pixels."""
from pathlib import Path
import xml.etree.ElementTree as ET


def elevation_path(directory, name):
    directory = Path(directory)
    virtual = directory/f'{name}.vrt'
    return virtual if virtual.is_file() else directory/f'{name}.tif'


def copy_virtual_raster(source, target):
    source, target = Path(source), Path(target)
    tree = ET.parse(source)
    for tag in ('SourceDataset', 'SourceFilename'):
        for element in tree.findall(f'.//{tag}'):
            if element.get('relativeToVRT') == '1':
                element.text = str((source.parent/element.text).resolve())
                element.set('relativeToVRT', '0')
    temporary = target.with_suffix('.partial.vrt')
    tree.write(temporary, encoding='utf-8', xml_declaration=True)
    temporary.replace(target)
