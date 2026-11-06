"""Regional diagnostic inputs must resolve from copied session projects."""
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from src.api.controllers.session_controller import _copy_and_fix_qgz


class SessionProjectPathsTest(unittest.TestCase):
    def test_sibling_and_parent_paths_preserve_targets_and_provider_options(self):
        with tempfile.TemporaryDirectory() as folder:
            server=Path(folder)/'server'
            server.mkdir()
            source=server/'starting-map.qgz'
            destination=server/'sessions/test/map.qgz'
            destination.parent.mkdir(parents=True)
            paths=['./pet.tif','../raster/svf.tif','./zones.gpkg|layername=zones']
            xml='<qgis>'+''.join(f'<datasource>{p}</datasource>' for p in paths)+'</qgis>'
            with zipfile.ZipFile(source,'w') as archive:
                archive.writestr('map.qgs',xml)
                archive.writestr('project-style.txt','preserve')
            _copy_and_fix_qgz(str(source),str(destination))
            with zipfile.ZipFile(destination) as archive:
                root=ET.fromstring(archive.read('map.qgs'))
                self.assertEqual(archive.read('project-style.txt'),b'preserve')
            for before,item in zip(paths,root.findall('datasource')):
                p1,_,options1=before.partition('|')
                p2,_,options2=item.text.partition('|')
                self.assertEqual((server/p1).resolve(),(destination.parent/p2).resolve())
                self.assertEqual(options1,options2)
