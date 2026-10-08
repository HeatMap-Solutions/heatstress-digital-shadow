"""Run inside QGIS Python; synthetic rasters only, no application imports."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import probe
from osgeo import gdal, osr
import numpy as np


def raster(path, values, origin=0):
    a = np.array(values, dtype="float32")
    ds = gdal.GetDriverByName("GTiff").Create(
        str(path), a.shape[1], a.shape[0], 1, gdal.GDT_Float32
    )
    ds.SetGeoTransform((origin, 1, 0, 10, 0, -1))
    s = osr.SpatialReference()
    s.ImportFromEPSG(28992)
    ds.SetProjection(s.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(-9999)
    ds.GetRasterBand(1).WriteArray(a)
    ds.FlushCache()
    ds = None


class RasterTests(unittest.TestCase):
    def test_difference_and_nodata_masks(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.tif", Path(tmp) / "b.tif"
            raster(a, [[10, 20], [-9999, 30]])
            raster(b, [[12, 20], [15, -9999]])
            d = probe.difference(str(a), str(b))
            self.assertTrue(d["compatible"])
            self.assertEqual(d["joint_valid_pixels"], 2)
            self.assertEqual(d["mean_absolute_difference"], 1)
            self.assertEqual(d["maximum_absolute_difference"], 2)
            self.assertEqual(d["nodata_mask_differences"], 2)

    def test_same_shape_different_origin_is_incompatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.tif", Path(tmp) / "b.tif"
            raster(a, [[1, 2]])
            raster(b, [[1, 2]], origin=1)
            self.assertFalse(probe.difference(str(a), str(b))["compatible"])

    def test_empty_output_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            raster(Path(tmp) / "empty.tif", [[-9999, -9999]])
            self.assertFalse(probe.validate(tmp, ["empty.tif"])["ok"])

    def test_valid_output_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            raster(Path(tmp) / "ok.tif", [[1, 2], [3, -9999]])
            result = probe.validate(tmp, ["ok.tif"])
            self.assertTrue(result["ok"])
            self.assertEqual(result["checks"][0]["raster"]["valid_pixels"], 3)

    def test_unreadable_output_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bad.tif").write_text("not a raster")
            self.assertFalse(probe.validate(tmp, ["bad.tif"])["ok"])


if __name__ == "__main__":
    unittest.main()
