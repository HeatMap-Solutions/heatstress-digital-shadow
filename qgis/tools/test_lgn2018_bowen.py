"""Tests for the standalone classifier. No QGIS runtime is needed."""

import unittest

from lgn2018_bowen import classify_lgn2018


class ClassifyLgn2018Test(unittest.TestCase):
    def test_high_bowen_classes(self):
        for code in (18, 19, 24, 25, 26, 27):
            with self.subTest(code=code):
                self.assertEqual(classify_lgn2018(code), (3.0, 0))

    def test_other_classes_include_water(self):
        for code in (1, 11, 16, 17, 28):
            with self.subTest(code=code):
                self.assertEqual(classify_lgn2018(code), (0.4, 1))

    def test_missing_values(self):
        for code in (None, 0, -9999, float("nan"), float("inf")):
            with self.subTest(code=code):
                self.assertIsNone(classify_lgn2018(code))

    def test_declared_nodata_takes_precedence_over_classification(self):
        self.assertIsNone(classify_lgn2018(18, nodata=18))
        self.assertIsNone(classify_lgn2018(65535, nodata=65535))

    def test_non_integer_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "integer LGN2018 class"):
            classify_lgn2018(18.5)


if __name__ == "__main__":
    unittest.main()
