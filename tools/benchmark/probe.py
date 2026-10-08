"""Container-side observation only: never imports or patches application services."""

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path


def save(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def request(url, definition, output, timeout):
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )
    if definition.get("session"):
        with opener.open("http://127.0.0.1:8000/session/init", timeout=20) as response:
            json.loads(response.read())
    payload = definition.get("payload")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=definition["method"],
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    result = {"status": "failed", "http_status": None}
    try:
        with opener.open(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            result.update(http_status=response.status, body=body)
            parsed = json.loads(body)
            if parsed.get("status") == "success":
                result["status"] = "completed"
    except urllib.error.HTTPError as exc:
        result.update(
            http_status=exc.code, body=exc.read().decode("utf-8", errors="replace")
        )
    except Exception as exc:
        result.update(status="indeterminate", error=f"{type(exc).__name__}: {exc}")
    result["elapsed_seconds"] = time.perf_counter() - start
    save(output, result)


def inspect_data(data, definition):
    from osgeo import gdal, ogr

    gdal.UseExceptions()
    checks = []
    for relative in definition["inputs"]:
        p = Path(data) / relative
        if not p.is_file():
            checks.append(
                {"path": relative, "ok": False, "reason": "Required input is missing"}
            )
        elif p.suffix.lower() in (".tif", ".tiff"):
            ds = gdal.Open(str(p), gdal.GA_ReadOnly)
            checks.append(
                {
                    "path": relative,
                    "ok": ds is not None,
                    "size": [ds.RasterXSize, ds.RasterYSize],
                    "transform": list(ds.GetGeoTransform()),
                    "crs": ds.GetProjection(),
                }
            )
    if definition.get("wind_field"):
        ds = ogr.Open(str(Path(data) / definition["wind_field"]["file"]), 0)
        layer = ds.GetLayer()
        fields = [f.GetName() for f in layer.schema]
        key = definition["wind_field"]["required"]
        checks.append(
            {
                "ok": key in fields,
                "required_field": key,
                "actual_fields": fields,
                "reason": "Generation consumer requires this exact field name",
            }
        )
    for point in definition.get("payload", {}).get("points", []):
        for relative in ("dsm.TIF", "raster/br-reproject.tif"):
            ds = gdal.Open(str(Path(data) / relative))
            gt = ds.GetGeoTransform()
            x = int((point["x"] - gt[0]) / gt[1])
            y = int((point["y"] - gt[3]) / gt[5])
            margin = math.ceil((point["radius"] + 1) / abs(gt[1]))
            valid = (
                margin <= x < ds.RasterXSize - margin
                and margin <= y < ds.RasterYSize - margin
            )
            if valid:
                import numpy as np

                a = ds.ReadAsArray(
                    x - margin, y - margin, 2 * margin + 1, 2 * margin + 1
                )
                nodata = ds.GetRasterBand(1).GetNoDataValue()
                valid = bool(np.isfinite(a).all() and (a != nodata).all())
            checks.append(
                {
                    "point": point,
                    "path": relative,
                    "ok": valid,
                    "reason": "Object footprint must lie within valid input cells",
                }
            )
    from qgis.core import Qgis

    return {
        "checks": checks,
        "ok": all(c["ok"] for c in checks),
        "versions": {"gdal": gdal.VersionInfo("--version"), "qgis": Qgis.QGIS_VERSION},
    }


def raster_summary(path):
    import numpy as np
    from osgeo import gdal

    ds = gdal.Open(str(path), gdal.GA_ReadOnly)
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    count = 0
    missing = 0
    low = None
    high = None
    for y in range(0, ds.RasterYSize, 256):
        a = band.ReadAsArray(0, y, ds.RasterXSize, min(256, ds.RasterYSize - y))
        valid = np.isfinite(a)
        if nodata is not None:
            valid &= a != nodata
        count += int(valid.sum())
        missing += int(a.size - valid.sum())
        if valid.any():
            mn, mx = float(a[valid].min()), float(a[valid].max())
            low = mn if low is None else min(low, mn)
            high = mx if high is None else max(high, mx)
    return {
        "size": [ds.RasterXSize, ds.RasterYSize],
        "transform": list(ds.GetGeoTransform()),
        "crs": ds.GetProjection(),
        "nodata": nodata if nodata is None or math.isfinite(nodata) else str(nodata),
        "valid_pixels": count,
        "missing_pixels": missing,
        "minimum": low,
        "maximum": high,
    }


def validate(root, patterns, generated_paths=None):
    checks = []
    for pattern in patterns:
        matches = sorted(Path(root).glob(pattern))
        if generated_paths is not None:
            matches = [path for path in matches
                       if path.relative_to(root).as_posix() in generated_paths]
        if not matches:
            checks.append(
                {
                    "pattern": pattern,
                    "ok": False,
                    "reason": "No newly generated matching output",
                }
            )
        for path in matches:
            try:
                relative = str(path.relative_to(root))
                if path.suffix == ".gpkg":
                    from osgeo import ogr

                    ds = ogr.Open(str(path), 0)
                    layer = ds.GetLayer()
                    fields = [f.GetName() for f in layer.schema]
                    checks.append(
                        {
                            "path": relative,
                            "ok": layer.GetFeatureCount() > 0 and "u_1_2" in fields,
                            "features": layer.GetFeatureCount(),
                            "fields": fields,
                        }
                    )
                elif path.suffix == ".json":
                    data = json.loads(path.read_text())
                    checks.append({"path": relative, "ok": bool(data)})
                elif path.suffix == ".qgz":
                    import zipfile, xml.etree.ElementTree as ET

                    with zipfile.ZipFile(path) as z:
                        project = ET.fromstring(
                            z.read(next(n for n in z.namelist() if n.endswith(".qgs")))
                        )
                    source = next(
                        (
                            ml.findtext("datasource")
                            for ml in project.findall(".//projectlayers/maplayer")
                            if ml.findtext("layername") == "pet-session-updated"
                        ),
                        None,
                    )
                    target = path.parent / source if source else None
                    checks.append(
                        {
                            "path": relative,
                            "ok": bool(
                                target
                                and target.is_file()
                                and str(target).endswith("_filled.tif")
                            ),
                            "updated_layer_source": source,
                        }
                    )
                else:
                    raster = raster_summary(path)
                    checks.append(
                        {
                            "path": relative,
                            "ok": raster["valid_pixels"] > 0,
                            "raster": raster,
                        }
                    )
            except Exception as exc:
                checks.append(
                    {
                        "path": str(path.relative_to(root)),
                        "ok": False,
                        "reason": str(exc),
                    }
                )
    return {
        "ok": bool(checks) and all(c["ok"] for c in checks),
        "checks": checks,
        "scope": "Fresh artifact, readability, grid metadata and valid-cell coverage; not scientific accuracy",
    }


def difference(first, second):
    import numpy as np
    from osgeo import gdal, osr

    a, b = gdal.Open(first), gdal.Open(second)
    same_grid = (
        (a.RasterXSize, a.RasterYSize) == (b.RasterXSize, b.RasterYSize)
        and a.GetGeoTransform() == b.GetGeoTransform()
        and bool(
            osr.SpatialReference(wkt=a.GetProjection()).IsSame(
                osr.SpatialReference(wkt=b.GetProjection())
            )
        )
    )
    if not same_grid:
        return {"compatible": False, "reason": "Grid mismatch; no resampling performed"}
    ba, bb = a.GetRasterBand(1), b.GetRasterBand(1)
    na, nb = ba.GetNoDataValue(), bb.GetNoDataValue()
    n = 0
    total = 0.0
    maximum = 0.0
    masks = 0
    for y in range(0, a.RasterYSize, 256):
        height = min(256, a.RasterYSize - y)
        aa = ba.ReadAsArray(0, y, a.RasterXSize, height).astype("float64")
        ab = bb.ReadAsArray(0, y, b.RasterXSize, height).astype("float64")
        va = np.isfinite(aa)
        vb = np.isfinite(ab)
        if na is not None:
            va &= aa != na
        if nb is not None:
            vb &= ab != nb
        masks += int((va != vb).sum())
        valid = va & vb
        d = np.abs(aa[valid] - ab[valid])
        n += d.size
        total += float(d.sum())
        if d.size:
            maximum = max(maximum, float(d.max()))
    return {
        "compatible": True,
        "joint_valid_pixels": n,
        "nodata_mask_differences": masks,
        "mean_absolute_difference": total / n if n else None,
        "maximum_absolute_difference": maximum if n else None,
        "interpretation": "Numerical difference only. Existing random foliage is uncontrolled; no accuracy threshold applied.",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["request", "inspect", "validate", "difference"])
    p.add_argument("args", nargs="+")
    a = p.parse_args()
    if a.action == "request":
        url, definition, output, timeout = a.args
        request(url, json.loads(Path(definition).read_text()), output, float(timeout))
    elif a.action == "inspect":
        root, definition, output = a.args
        save(output, inspect_data(root, json.loads(Path(definition).read_text())))
    elif a.action == "validate":
        root, definition, output = a.args[:3]
        generated_paths = None
        if len(a.args) > 3:
            generated_paths = {
                item["path"][len("outputs/"):]
                for item in json.loads(Path(a.args[3]).read_text())
                if item["path"].startswith("outputs/")
            }
        save(
            output,
            validate(
                root, json.loads(Path(definition).read_text())["expected_outputs"], generated_paths
            ),
        )
    else:
        first, second, output = a.args
        save(output, difference(first, second))
