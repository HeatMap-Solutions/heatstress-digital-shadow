# Bowen ratio and vegetation proxy from LGN2018

Use this rule to generate two PET inputs from a categorical LGN2018 land-use
raster. It is the base-map rule used by the Zeeland generation workflow and
matches the documented Middelburg Bowen method.

| LGN2018 class | Bowen ratio | Vegetation proxy |
| --- | ---: | ---: |
| 18, 19, 24, 25, 26, 27 | 3.0 | 0 |
| Other valid positive classes, including water (16 and 17) | 0.4 | 1 |
| Missing, masked, or non-positive | NoData | NoData |

The proxy marks the low-Bowen category. Water receives a 1, so the average
proxy value in a zone can include water. It is different from measured
vegetation cover, NDVI, and tree canopy height. The LGN2024 class list differs
and requires its own mapping.

[LGN downloads](https://lgn.nl/bestanden) and the
[upstream Bowen methodology](https://github.com/jrcz-data-science-lab/heatstress-digital-shadow/wiki/Bowen-ratio-map)
(revised 23 February 2026).

## Reuse from Python

Copy `lgn2018_bowen.py` into your project or add `qgis/tools` to your Python
import path. It requires Python 3.9+ and the standard library.

```python
from lgn2018_bowen import classify_lgn2018

bowen, vegetation = classify_lgn2018(25)  # (3.0, 0)
assert classify_lgn2018(65535, nodata=65535) is None
```

For large rasters, use the vectorized workflow below instead of looping
through individual pixels in Python.

## Generate rasters with GDAL

This example requires GDAL command-line tools and the Python GDAL and NumPy
packages used by `gdal_calc.py`. Run it in a fresh output directory.

1. Obtain the LGN2018 GeoTIFF and choose a target raster grid. Use the exact
   bounds, width, height and CRS of your PET reference grid. The example uses
   the documented Middelburg extent in RD New (EPSG:28992) at 1 m. At 2 m,
   the same extent would use `-ts 2500 3125`.
2. Align the categorical classes using nearest-neighbour sampling, then
   classify the aligned cells. With a 1 m output, a native 5 m cell can supply
   25 output cells when the grids line up. The source land-use detail stays 5 m.
3. Use `gdalinfo` to check both outputs against the target grid. Check CRS,
   geotransform, extent, dimensions and NoData. Keep a record of the source
   edition, file hash, target grid and GDAL version.

```bash
gdalwarp -of VRT -t_srs EPSG:28992 -te 30000 387500 35000 393750 \
  -ts 5000 6250 -r near -ovr NONE -ot Int32 -dstnodata -9999 LGN2018.tif classes.vrt

high_bowen='isin(A, [18, 19, 24, 25, 26, 27])'
gdal_calc.py -A classes.vrt --type=Float32 --NoDataValue=-9999 \
  --calc="where(A > 0, where(${high_bowen}, 3.0, 0.4), -9999)" \
  --co=TILED=YES --co=COMPRESS=DEFLATE --co=BIGTIFF=IF_SAFER \
  --outfile=br-reproject.tif

gdal_calc.py -A classes.vrt --type=Float32 --NoDataValue=-9999 \
  --calc="where(A > 0, 1 - ${high_bowen}, -9999)" \
  --co=TILED=YES --co=COMPRESS=DEFLATE --co=BIGTIFF=IF_SAFER \
  --outfile=vegetation-reproject.tif

gdalinfo br-reproject.tif
gdalinfo vegetation-reproject.tif
```

Keep the VRT next to its source during processing. The warp honours the
declared source NoData. If your input uses an undeclared NoData sentinel,
supply it explicitly with `-srcnodata`. The example writes a rectangular
extent. Apply your project's land or region mask separately when needed.

## Verification

Run the classification tests from the repository root.

```bash
python3 -m unittest discover -s qgis/tools -p 'test_lgn2018_bowen.py' -v
```

A prior local audit on 12 October 2026 found zero mismatches across all
1,250,000 supplied Middelburg display Bowen pixels and 31,250,000 pixels in
each of the supplied calculation rasters. The audit compared LGN2018
nearest-neighbour samples with the existing outputs. It established
compatibility with those files. The original LGN edition used to produce them
is undocumented, and independent scientific accuracy remains unverified.
