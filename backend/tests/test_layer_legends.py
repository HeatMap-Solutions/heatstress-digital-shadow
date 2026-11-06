"""Diagnostic layers must not inherit the PET temperature legend."""
from pathlib import Path
import tempfile
import unittest
import zipfile
from src.api.controllers.legend_controller import LegendController


class LayerLegendsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        (root/'server').mkdir()
        layers=[]
        for name,low,high in [('pet-version-1',20,40),('bowen-ratio-middelburg',.4,3)]:
            layers.append(f'<maplayer><layername>{name}</layername><pipe><rasterrenderer type="singlebandpseudocolor" band="1" classificationMin="{low}" classificationMax="{high}"><rastershader><colorrampshader colorRampType="INTERPOLATED"><item value="{low}" color="#123456"/><item value="{high}" color="#abcdef"/></colorrampshader></rastershader></rasterrenderer></pipe></maplayer>')
        layers.append('<maplayer><layername>wind</layername><renderer-v2 type="graduatedSymbol"><ranges><range lower="6" upper="8" symbol="0" label="6 - 8 m/s"/></ranges><symbols><symbol name="0"><layer><Option><Option name="color" value="239,138,98,255,rgb:0.9,0.5,0.3,1"/></Option></layer></symbol></symbols></renderer-v2></maplayer>')
        with zipfile.ZipFile(root/'server/starting-map.qgz','w') as archive:
            archive.writestr('map.qgs','<qgis><projectlayers>'+''.join(layers)+'</projectlayers></qgis>')
        self.controller=LegendController(root)

    def tearDown(self):
        self.temp.cleanup()

    def test_bowen_uses_its_own_values(self):
        legend=self.controller.get_legend('bowen-ratio-middelburg')
        self.assertEqual(legend['renderer']['classification_min'],.4)
        self.assertEqual(legend['renderer']['classification_max'],3)
        self.assertEqual([item['value'] for item in legend['color_ramp']['items']],[.4,3])

    def test_updated_pet_inherits_the_base_legend(self):
        self.assertEqual(self.controller.get_legend('pet-session-updated'),self.controller.get_legend())

    def test_wind_preserves_class_labels_and_valid_rgba(self):
        legend=self.controller.get_legend('wind')
        self.assertEqual(legend['color_ramp']['mode'],'discrete')
        self.assertEqual(legend['color_ramp']['items'],[{'value':6.,'label':'6 - 8 m/s','color':'239,138,98,255'}])

    def test_unknown_layer_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Unknown map layer'):
            self.controller.get_legend('missing')
