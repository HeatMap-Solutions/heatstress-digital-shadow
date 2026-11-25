"""Compare our PET map with a published reference map (e.g. the province's Hittestresskaart).

Both maps are in RD New (EPSG:28992), so the same x, y is the same spot on both.
Random points are drawn from OUR map (only pixels with a value), with a fixed seed
so every run uses the same points. For each point the reference value is asked from
the reference WMS (GetFeatureInfo), and the differences are summarised.

Used by tools/compare_hittestresskaart.py. The reference service and layers are in
src/configs/area.py (REFERENCE_WMS, REFERENCE_LAYERS).
"""
from dataclasses import dataclass, asdict
import json
import time
import urllib.parse
import urllib.request

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()

PLAUSIBLE_PET = (-30.0, 80.0)   # anything outside this is treated as "no value" (NoData codes)


@dataclass
class Point:
    id: int
    x: float            # RD New, metres (pixel centre)
    y: float
    lon: float          # WGS84, for looking the point up in the app
    lat: float
    ours: float         # our PET (°C)
    in_shade: bool      # our pixel uses the shade formula (PET equals the shade PET)
    svf: float          # our sky view factor at that pixel


class RasterReader:
    """Reads single pixels from a raster by RD coordinate; None for NoData or outside."""

    def __init__(self, path):
        self.ds = gdal.Open(str(path))
        if self.ds is None:
            raise FileNotFoundError(path)
        self.band = self.ds.GetRasterBand(1)
        self.nodata = self.band.GetNoDataValue()
        self.transform = self.ds.GetGeoTransform()

    def pixel(self, col, row):
        if not (0 <= col < self.ds.RasterXSize and 0 <= row < self.ds.RasterYSize):
            return None
        value = float(self.band.ReadAsArray(int(col), int(row), 1, 1)[0, 0])
        if not np.isfinite(value) or (self.nodata is not None and value == self.nodata):
            return None
        return value

    def at(self, x, y):
        left, size_x, _, top, _, size_y = self.transform
        return self.pixel(int((x-left)//size_x), int((y-top)//size_y))


def to_wgs84():
    rd, wgs = osr.SpatialReference(), osr.SpatialReference()
    rd.ImportFromEPSG(28992)
    wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)   # lon, lat
    return osr.CoordinateTransformation(rd, wgs)


def sample_points(pet_path, count, seed, shadow_path=None, svf_path=None, max_tries=100):
    """`count` random pixels of our map that have a value. Same seed -> same points."""
    pet = RasterReader(pet_path)
    shadow = RasterReader(shadow_path) if shadow_path else None
    svf = RasterReader(svf_path) if svf_path else None
    rng = np.random.default_rng(seed)
    left, size, _, top, _, _ = pet.transform
    transform = to_wgs84()
    points, tries = [], 0
    while len(points) < count:
        tries += 1
        if tries > count*max_tries:
            raise RuntimeError(f'Found only {len(points)} points with a value after {tries-1} tries')
        col = int(rng.integers(pet.ds.RasterXSize))
        row = int(rng.integers(pet.ds.RasterYSize))
        value = pet.pixel(col, row)
        if value is None:
            continue
        x, y = left + (col+0.5)*size, top - (row+0.5)*size
        lon, lat, _ = transform.TransformPoint(x, y)
        shade_value = shadow.at(x, y) if shadow else None
        points.append(Point(id=len(points)+1, x=x, y=y, lon=round(lon, 6), lat=round(lat, 6), ours=value,
                            in_shade=shade_value is not None and shade_value == value,
                            svf=round(svf.at(x, y), 3) if svf and svf.at(x, y) is not None else None))
    return points


def wms_value(service, layer, x, y, timeout=60, retries=3):
    """The reference value at RD x, y with WMS GetFeatureInfo; None if the service has no value there."""
    workspace = layer.split(':')[0]
    params = {'SERVICE': 'WMS', 'VERSION': '1.1.1', 'REQUEST': 'GetFeatureInfo', 'LAYERS': layer,
              'QUERY_LAYERS': layer, 'STYLES': '', 'SRS': 'EPSG:28992',
              'BBOX': f'{x-0.5},{y-0.5},{x+0.5},{y+0.5}', 'WIDTH': 1, 'HEIGHT': 1, 'X': 0, 'Y': 0,
              'INFO_FORMAT': 'application/json'}
    url = service.format(workspace=workspace) + '?' + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                data = json.loads(response.read())
            break
        except Exception:
            if attempt == retries-1:
                raise
            time.sleep(2*(attempt+1))
    features = data.get('features') or []
    if not features:
        return None
    value = (features[0].get('properties') or {}).get('GRAY_INDEX')
    if value is None or not np.isfinite(value) or not PLAUSIBLE_PET[0] <= value <= PLAUSIBLE_PET[1]:
        return None
    return float(value)


def summarise(ours, theirs):
    """Statistics of ours - theirs over the points where both have a value."""
    pairs = [(a, b) for a, b in zip(ours, theirs) if a is not None and b is not None]
    if not pairs:
        return {'compared': 0}
    a, b = np.array(pairs).T
    diff = a - b
    within = lambda limit: round(float(np.mean(np.abs(diff) <= limit))*100, 1)
    return {
        'compared': len(pairs),
        'ours_mean': round(float(a.mean()), 2),
        'reference_mean': round(float(b.mean()), 2),
        'mean_difference': round(float(diff.mean()), 2),          # > 0: our map is warmer
        'mean_abs_difference': round(float(np.abs(diff).mean()), 2),
        'rmse': round(float(np.sqrt((diff**2).mean())), 2),
        'correlation': round(float(np.corrcoef(a, b)[0, 1]), 3) if len(pairs) > 2 and a.std() and b.std() else None,
        'within_1C_pct': within(1), 'within_2C_pct': within(2), 'within_3C_pct': within(3),
    }


def compare(points, layers, service, fetch=wms_value):
    """Reference values per layer and a summary: overall, in the sun and in the shade (our classification)."""
    values = {layer: [fetch(service, layer, p.x, p.y) for p in points] for layer in layers}
    summary = {}
    for layer, theirs in values.items():
        ours = [p.ours for p in points]
        sun = [p.ours if not p.in_shade else None for p in points]
        shade = [p.ours if p.in_shade else None for p in points]
        summary[layer] = {'all': summarise(ours, theirs), 'sun': summarise(sun, theirs),
                          'shade': summarise(shade, theirs),
                          'reference_no_value': sum(v is None for v in theirs)}
    return values, summary


def rows(points, values):
    """One dict per point for the CSV."""
    for i, point in enumerate(points):
        row = asdict(point)
        for layer, theirs in values.items():
            row[layer] = theirs[i]
            row[f'diff {layer}'] = None if theirs[i] is None else round(point.ours-theirs[i], 3)
        yield row