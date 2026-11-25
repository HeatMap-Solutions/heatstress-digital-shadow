#!/usr/bin/env python3
"""Compare two PET maps (VRT or GeoTIFF) pixel by pixel on their common area.

    python3 tools/compare_pet.py /data/zeeland-tiles/pilot/pet.vrt /data/zeeland-tiles/zeeland-1m/pet.vrt
    python3 tools/compare_pet.py A B --diff /data/validation/a-minus-b.tif     # also write the difference map

Both maps must have the same pixel size and lie on the same pixel grid (as all our runs
and the original Middelburg map do). Differences are A - B, so > 0 means A is warmer.
Exit code 0 only when the maps are identical (used as a regression check).
"""
import argparse
import json
import math

import numpy as np
from osgeo import gdal, osr

BLOCK_ROWS = 512
NODATA = -9999.0


def valid(values, nodata):
    mask = np.isfinite(values) & (values < 1e30)            # 3.4e38 = float32 max, a common NoData code
    if nodata is not None:
        mask &= values != nodata
    return mask


def main(a_path, b_path, diff_path=None):
    gdal.UseExceptions()
    a, b = gdal.Open(a_path), gdal.Open(b_path)
    ta, tb = a.GetGeoTransform(), b.GetGeoTransform()
    if ta[1] != tb[1] or ta[5] != tb[5]:
        raise SystemExit('The two maps have different pixel sizes')
    res = ta[1]
    if (ta[0]-tb[0]) % res or (ta[3]-tb[3]) % res:
        raise SystemExit('The two maps are not on the same pixel grid')
    left, top = max(ta[0], tb[0]), min(ta[3], tb[3])
    right = min(ta[0]+a.RasterXSize*res, tb[0]+b.RasterXSize*res)
    bottom = max(ta[3]-a.RasterYSize*res, tb[3]-b.RasterYSize*res)
    if right <= left or top <= bottom:
        raise SystemExit('The two maps do not overlap')
    cols, rows = round((right-left)/res), round((top-bottom)/res)
    ax, ay = round((left-ta[0])/res), round((ta[3]-top)/res)
    bx, by = round((left-tb[0])/res), round((tb[3]-top)/res)
    na, nb = a.GetRasterBand(1).GetNoDataValue(), b.GetRasterBand(1).GetNoDataValue()

    out = None
    if diff_path:
        out = gdal.GetDriverByName('GTiff').Create(diff_path, cols, rows, 1, gdal.GDT_Float32,
                                                   ['TILED=YES', 'COMPRESS=DEFLATE', 'PREDICTOR=3', 'BIGTIFF=IF_SAFER'])
        out.SetGeoTransform((left, res, 0, top, 0, -res))
        crs = osr.SpatialReference()
        crs.ImportFromEPSG(28992)
        out.SetProjection(crs.ExportToWkt())
        out.GetRasterBand(1).SetNoDataValue(NODATA)

    stats = {'compared_pixels': 0, 'both_valid': 0, 'valid_in_one_only': 0,
             'valid_in_a_only': 0, 'valid_in_b_only': 0,
             'different_values': 0, 'max_abs_difference': 0.0}
    limits = (0.1, 0.5, 1.0, 2.0)
    within = dict.fromkeys(limits, 0)
    sums = dict.fromkeys(('d', 'ad', 'dd', 'a', 'b', 'aa', 'bb', 'ab'), 0.0)
    for y in range(0, rows, BLOCK_ROWS):
        h = min(BLOCK_ROWS, rows-y)
        va = a.GetRasterBand(1).ReadAsArray(ax, ay+y, cols, h).astype('float64')
        vb = b.GetRasterBand(1).ReadAsArray(bx, by+y, cols, h).astype('float64')
        ma, mb = valid(va, na), valid(vb, nb)
        both = ma & mb
        pa, pb = va[both], vb[both]
        diff = pa - pb
        stats['compared_pixels'] += va.size
        stats['both_valid'] += int(both.sum())
        stats['valid_in_a_only'] += int((ma & ~mb).sum())
        stats['valid_in_b_only'] += int((mb & ~ma).sum())
        stats['different_values'] += int((diff != 0).sum())
        if diff.size:
            stats['max_abs_difference'] = max(stats['max_abs_difference'], float(np.abs(diff).max()))
        for limit in limits:
            within[limit] += int((np.abs(diff) <= limit).sum())
        for key, values in (('d', diff), ('ad', np.abs(diff)), ('dd', diff*diff), ('a', pa), ('b', pb),
                            ('aa', pa*pa), ('bb', pb*pb), ('ab', pa*pb)):
            sums[key] += float(values.sum())
        if out is not None:
            block = np.full(va.shape, NODATA, dtype='float32')
            block[both] = diff
            out.GetRasterBand(1).WriteArray(block, 0, y)
    stats['valid_in_one_only'] = stats['valid_in_a_only'] + stats['valid_in_b_only']

    n = stats['both_valid']
    if n:
        mean_a, mean_b = sums['a']/n, sums['b']/n
        var_a, var_b = sums['aa']/n - mean_a**2, sums['bb']/n - mean_b**2
        stats.update({
            'mean_a': round(mean_a, 3), 'mean_b': round(mean_b, 3),
            'mean_difference': round(sums['d']/n, 4),                 # A - B
            'mean_abs_difference': round(sums['ad']/n, 4),
            'rmse': round(math.sqrt(sums['dd']/n), 4),
            'correlation': round((sums['ab']/n - mean_a*mean_b)/math.sqrt(var_a*var_b), 4) if var_a > 0 and var_b > 0 else None,
            **{f'within_{limit}C_pct': round(within[limit]/n*100, 2) for limit in limits},
        })
    if out is not None:
        out = None
        stats['difference_map'] = diff_path
    print(json.dumps(stats, indent=2))
    return 0 if stats['valid_in_one_only'] == 0 and stats['different_values'] == 0 else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('a', help='first map (A)')
    parser.add_argument('b', help='second map (B)')
    parser.add_argument('--diff', help='write the difference map A - B as a GeoTIFF here')
    args = parser.parse_args()
    raise SystemExit(main(args.a, args.b, args.diff))