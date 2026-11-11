#!/usr/bin/env python3
"""Compare two PET maps (VRT or GeoTIFF) on their common area, block by block.

    python3 tools/compare_pet.py /out/pilot-2m-tiled/pet.vrt /out/pilot-2m-single/pet.vrt
"""
import json
import sys

import numpy as np
from osgeo import gdal

BLOCK_ROWS = 512


def main(a_path, b_path):
    gdal.UseExceptions()
    a, b = gdal.Open(a_path), gdal.Open(b_path)
    ta, tb = a.GetGeoTransform(), b.GetGeoTransform()
    if ta[1] != tb[1] or ta[5] != tb[5]:
        raise SystemExit('The two maps have different pixel sizes')
    res = ta[1]
    left, top = max(ta[0], tb[0]), min(ta[3], tb[3])
    right = min(ta[0]+a.RasterXSize*res, tb[0]+b.RasterXSize*res)
    bottom = max(ta[3]-a.RasterYSize*res, tb[3]-b.RasterYSize*res)
    cols, rows = round((right-left)/res), round((top-bottom)/res)
    ax, ay = round((left-ta[0])/res), round((ta[3]-top)/res)
    bx, by = round((left-tb[0])/res), round((tb[3]-top)/res)
    na, nb = a.GetRasterBand(1).GetNoDataValue(), b.GetRasterBand(1).GetNoDataValue()
    stats = {'compared_pixels': 0, 'both_valid': 0, 'valid_in_one_only': 0,
             'different_values': 0, 'max_abs_difference': 0.0}
    for y in range(0, rows, BLOCK_ROWS):
        h = min(BLOCK_ROWS, rows-y)
        va = a.GetRasterBand(1).ReadAsArray(ax, ay+y, cols, h)
        vb = b.GetRasterBand(1).ReadAsArray(bx, by+y, cols, h)
        ma, mb = np.isfinite(va) & (va != na), np.isfinite(vb) & (vb != nb)
        both = ma & mb
        diff = np.abs(va[both]-vb[both])
        stats['compared_pixels'] += va.size
        stats['both_valid'] += int(both.sum())
        stats['valid_in_one_only'] += int((ma ^ mb).sum())
        stats['different_values'] += int((diff > 0).sum())
        if diff.size:
            stats['max_abs_difference'] = max(stats['max_abs_difference'], float(diff.max()))
    print(json.dumps(stats, indent=2))
    return 0 if stats['valid_in_one_only'] == 0 and stats['different_values'] == 0 else 1


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
    