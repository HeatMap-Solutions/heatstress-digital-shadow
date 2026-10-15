#!/usr/bin/env python3
"""
Prepare the five PET-pipeline inputs for another area (e.g. Vlissingen).

The PET pipeline (`GET /pet/full-map-generation`) reads fixed paths under /data:

    dsm.TIF                              AHN surface model, 0.5 m
    raster/svf-reproject.tif             sky view factor, 1 m
    raster/br-reproject.tif              Bowen ratio, 1 m
    raster/vegetation-reproject.tif      vegetation fraction, 1 m
    json/wind_reduction copy.geojson     wind grid (140 x 280 m) with field u_1_2

This script builds those files for a new area in a separate data folder, so
switching city means pointing the containers at another folder
(DATA_DIR=./data-vlissingen docker compose up) instead of overwriting ./data.

Sources (same as the hand-made Middelburg data, but scripted):
    DSM          PDOK AHN WCS (coverage dsm_05m, currently AHN4)
    SVF          KNMI Data Platform, dataset SVF_NL v3 (0.5 m sheets)
    Bowen/veg    LGN2018 land-use raster (download from lgn.nl, pass with --lgn)
    Wind         placeholder constant (wind is another team's scope)

Run it inside the qgis container, which already has GDAL and numpy:

    docker compose run --rm --no-deps \
        -v ./data-vlissingen:/out -v ./sources:/sources \
        qgis python3 /app/tools/prepare_area.py build \
        --area vlissingen --out /out --lgn /sources/LGN2018.tif --reference /data

Check the Bowen/vegetation rule against the existing Middelburg files first:

    docker compose run --rm --no-deps -v ./sources:/sources \
        qgis python3 /app/tools/prepare_area.py verify-middelburg \
        --lgn /sources/LGN2018.tif --reference /data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()

# ── Area presets ────────────────────────────────────────────────────────────
# The Middelburg wind grid starts at (30000, 393750) with cells 140 m (x) by
# 280 m (y). New extents are snapped to that same lattice so a later
# Walcheren/Zeeland grid lines up with every area made here.
GRID_ORIGIN = (30000.0, 393750.0)
CELL_W, CELL_H = 140.0, 280.0

AREAS = {
    "middelburg": {
        "name": "Middelburg",
        "lat": 51.498,
        "lon": 3.613,
        # Not on the lattice in y; this is the existing AHN sheet 65DN1 extent.
        "extent": (30000.0, 387500.0, 35000.0, 393750.0),
        "svf_sheets": ["65dn1"],
    },
    "vlissingen": {
        "name": "Vlissingen",
        "lat": 51.4425,
        "lon": 3.5735,
        # Westduin/Dishoek in the west to Oost-Souburg in the east, boulevard
        # in the south to the Souburg/Middelburg edge in the north.
        # 50 x 16 wind cells = 7.0 x 4.48 km (similar size to Middelburg).
        "extent": (25800.0, 383670.0, 32800.0, 388150.0),
        "svf_sheets": ["65cn2", "65cz2", "65dn1", "65dz1"],
    },
}

# LGN2018 classes treated as paved (wiki "Bowen ratio map"). Bowen 3 / veg 0.
PAVED_CLASSES = (18, 19, 24, 25, 26, 27)
BOWEN_PAVED, BOWEN_OTHER = 3.0, 0.4

AHN_WCS = "https://service.pdok.nl/rws/ahn/wcs/v1_0"
KNMI_API = "https://api.dataplatform.knmi.nl/open-data/v1"
KNMI_DATASET, KNMI_VERSION = "SVF_NL", "3"
# Public anonymous key published on developer.dataplatform.knmi.nl (shared,
# rate-limited, valid until 2027-08-01). Set KNMI_API_KEY to use your own.
KNMI_ANON_KEY = (
    "eyJvcmciOiI1ZTU1NGUxOTI3NGE5NjAwMDEyYTNlYjEiLCJpZCI6IjUzYTg1ZDBhMmQ5YzRkYzJi"
    "YWNlNzQ4NTQ2Zjk4ODExIiwiaCI6Im11cm11cjEyOCJ9"
)

GTIFF_OPTS = ["TILED=YES", "COMPRESS=DEFLATE", "BLOCKXSIZE=512", "BLOCKYSIZE=512", "BIGTIFF=IF_SAFER"]
RD = "EPSG:28992"
WIND_FILE = "wind_reduction copy.geojson"  # name hardcoded in pet_controller.py
DEFAULT_U12 = 0.5


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Extent helpers ──────────────────────────────────────────────────────────
def check_on_lattice(extent) -> None:
    xmin, ymin, xmax, ymax = extent
    for v, o, step, label in (
        (xmin, GRID_ORIGIN[0], CELL_W, "xmin"), (xmax, GRID_ORIGIN[0], CELL_W, "xmax"),
        (ymin, GRID_ORIGIN[1], CELL_H, "ymin"), (ymax, GRID_ORIGIN[1], CELL_H, "ymax"),
    ):
        if abs(((v - o) / step) - round((v - o) / step)) > 1e-9:
            raise SystemExit(f"{label}={v} is not on the wind-grid lattice ({o} + n*{step})")


def snap_to_lattice(xmin, ymin, xmax, ymax):
    """Snap a bbox outward to the Middelburg wind-grid lattice."""
    import math
    ox, oy = GRID_ORIGIN
    return (
        ox + math.floor((xmin - ox) / CELL_W) * CELL_W,
        oy + math.floor((ymin - oy) / CELL_H) * CELL_H,
        ox + math.ceil((xmax - ox) / CELL_W) * CELL_W,
        oy + math.ceil((ymax - oy) / CELL_H) * CELL_H,
    )


# ── HTTP helpers ────────────────────────────────────────────────────────────
def http_get(url: str, headers: dict | None = None, retries: int = 4, timeout: int = 300) -> bytes:
    last = None
    busy = 0
    attempt = 0
    while attempt < retries:
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read()[:500].decode("utf-8", "replace")
            if e.code in (400, 401, 403, 404):
                raise RuntimeError(f"HTTP {e.code} for {url}\n{body}") from e
            last = e
            if e.code == 429 and busy < 8:
                # Rate limited: wait (honouring Retry-After) without using up a retry.
                busy += 1
                try:
                    wait = int(e.headers.get("Retry-After", "")) + 1
                except ValueError:
                    wait = 30 * busy
                log(f"  server busy (429), waiting {wait}s ({busy}/8)")
                time.sleep(wait)
                continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
        attempt += 1
        wait = 5 * attempt
        log(f"  request failed ({last}); retry in {wait}s")
        time.sleep(wait)
    if isinstance(last, urllib.error.HTTPError) and last.code == 429 and "knmi" in url:
        raise RuntimeError(
            "KNMI keeps answering 'Too Many Requests'. The shared anonymous key is used up. "
            "Get a free personal key (see qgis/tools/README.md) and pass it with -e KNMI_API_KEY=<key>.")
    raise RuntimeError(f"Giving up on {url}: {last}")


def download_to(url: str, path: str, headers: dict | None = None) -> str:
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(http_get(url, headers))
    os.replace(tmp, path)
    return path


# ── DSM from AHN WCS ────────────────────────────────────────────────────────
def wcs_axis_labels(coverage: str) -> tuple[str, str]:
    q = urllib.parse.urlencode({"SERVICE": "WCS", "VERSION": "2.0.1",
                                "REQUEST": "DescribeCoverage", "COVERAGEID": coverage})
    xml = http_get(f"{AHN_WCS}?{q}").decode("utf-8", "replace")
    m = re.search(r'axisLabels="([^"]+)"', xml)
    if not m:
        log("  DescribeCoverage gave no axisLabels; assuming 'x y'")
        return "x", "y"
    labels = m.group(1).split()
    return labels[0], labels[1]


def fetch_ahn(coverage: str, extent, cache_dir: str, chunk: float = 1000.0) -> list[str]:
    os.makedirs(cache_dir, exist_ok=True)
    ax, ay = wcs_axis_labels(coverage)
    xmin, ymin, xmax, ymax = extent
    tiles = []
    xs = np.arange(xmin, xmax, chunk)
    ys = np.arange(ymin, ymax, chunk)
    total = len(xs) * len(ys)
    n = 0
    for x0 in xs:
        for y0 in ys:
            n += 1
            x1, y1 = min(x0 + chunk, xmax), min(y0 + chunk, ymax)
            path = os.path.join(cache_dir, f"{coverage}_{int(x0)}_{int(y0)}_{int(x1)}_{int(y1)}.tif")
            if os.path.exists(path):
                tiles.append(path)
                continue
            q = urllib.parse.urlencode([
                ("SERVICE", "WCS"), ("VERSION", "2.0.1"), ("REQUEST", "GetCoverage"),
                ("COVERAGEID", coverage), ("FORMAT", "image/tiff"),
                ("SUBSET", f"{ax}({x0:.1f},{x1:.1f})"), ("SUBSET", f"{ay}({y0:.1f},{y1:.1f})"),
            ])
            log(f"  {coverage} tile {n}/{total}")
            data = http_get(f"{AHN_WCS}?{q}")
            if data[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
                raise RuntimeError(f"WCS did not return a GeoTIFF:\n{data[:500].decode('utf-8', 'replace')}")
            with open(path + ".part", "wb") as f:
                f.write(data)
            os.replace(path + ".part", path)
            tiles.append(path)
    return tiles


# ── SVF from KNMI ───────────────────────────────────────────────────────────
def knmi_headers() -> dict:
    return {"Authorization": os.environ.get("KNMI_API_KEY", KNMI_ANON_KEY)}


def knmi_list_files() -> list[str]:
    names, token = [], None
    base = f"{KNMI_API}/datasets/{KNMI_DATASET}/versions/{KNMI_VERSION}/files"
    while True:
        params = {"maxKeys": "1000"}
        if token:
            params["nextPageToken"] = token
        page = json.loads(http_get(f"{base}?{urllib.parse.urlencode(params)}", knmi_headers()))
        names += [f["filename"] for f in page.get("files", [])]
        token = page.get("nextPageToken")
        if not token or not page.get("isTruncated", True):
            return names


def knmi_download(fn: str, cache_dir: str) -> str:
    path = os.path.join(cache_dir, fn)
    if not os.path.exists(path):
        meta = json.loads(http_get(
            f"{KNMI_API}/datasets/{KNMI_DATASET}/versions/{KNMI_VERSION}/files/"
            f"{urllib.parse.quote(fn)}/url", knmi_headers()))
        log(f"  downloading {fn}")
        download_to(meta["temporaryDownloadUrl"], path)
    return path


def fetch_svf(sheets: list[str], cache_dir: str) -> list[str]:
    os.makedirs(cache_dir, exist_ok=True)
    using = "personal key" if os.environ.get("KNMI_API_KEY") else "shared anonymous key"
    log(f"  KNMI SVF_NL v3 ({using})")
    available = None
    rasters = []
    for sheet in sheets:
        cached = [f for f in os.listdir(cache_dir)
                  if sheet.lower() in f.lower() and not f.endswith(".part")
                  and f.lower().endswith((".tif", ".tiff", ".zip"))]
        if cached:
            log(f"  {sheet}: already downloaded")
            for fn in cached:
                rasters += unpack_rasters(os.path.join(cache_dir, fn), cache_dir)
            continue
        # Try the known file name first (Middelburg's sheet is SVF_r65dn1.tif); one request per sheet.
        got = None
        for guess in (f"SVF_r{sheet}.tif", f"SVF_r{sheet}.zip", f"SVF_R{sheet.upper()}.tif"):
            try:
                got = knmi_download(guess, cache_dir)
                break
            except RuntimeError as e:
                if "HTTP 404" not in str(e) and "HTTP 400" not in str(e):
                    raise
        if got:
            rasters += unpack_rasters(got, cache_dir)
            continue
        if available is None:
            log("  listing KNMI SVF_NL v3 files")
            available = knmi_list_files()
        matches = [f for f in available if sheet.lower() in f.lower()]
        if not matches:
            sample = ", ".join(available[:5])
            raise RuntimeError(f"No SVF file for sheet '{sheet}'. First files in dataset: {sample}")
        for fn in matches:
            rasters += unpack_rasters(knmi_download(fn, cache_dir), cache_dir)
    return rasters


def unpack_rasters(path: str, cache_dir: str) -> list[str]:
    if not path.lower().endswith(".zip"):
        return [path]
    out = []
    with zipfile.ZipFile(path) as z:
        for member in z.namelist():
            if member.lower().endswith((".tif", ".tiff")):
                target = os.path.join(cache_dir, os.path.basename(member))
                if not os.path.exists(target):
                    with z.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                out.append(target)
    return out


# ── Raster helpers ──────────────────────────────────────────────────────────
def warp(sources, out_path, extent, res, resampling, src_srs=None, dst_type=None,
         src_nodata=None, dst_nodata=None):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    vrt = gdal.BuildVRT("", sources) if isinstance(sources, list) else sources
    kw = dict(
        format="GTiff", dstSRS=RD, outputBounds=extent, xRes=res, yRes=res,
        targetAlignedPixels=True, resampleAlg=resampling, creationOptions=GTIFF_OPTS,
        multithread=True, warpOptions=["NUM_THREADS=ALL_CPUS"],
    )
    if src_srs:
        kw["srcSRS"] = src_srs
    if dst_type is not None:
        kw["outputType"] = dst_type
    if src_nodata is not None:
        kw["srcNodata"] = src_nodata
    if dst_nodata is not None:
        kw["dstNodata"] = dst_nodata
    ds = gdal.Warp(out_path, vrt, **kw)
    ds = None
    return out_path


def coverage_fraction(path: str) -> float:
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray()
    nd = band.GetNoDataValue()
    if nd is None:
        return 1.0
    valid = ~np.isclose(arr, nd) if np.issubdtype(arr.dtype, np.floating) else arr != nd
    if np.issubdtype(arr.dtype, np.floating):
        valid &= ~np.isnan(arr)
    return float(valid.mean())


def open_lgn(path: str) -> str:
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            inner = [m for m in z.namelist() if m.lower().endswith((".tif", ".tiff", ".img"))]
        if not inner:
            raise SystemExit(f"No .tif/.img raster inside {path}")
        return f"/vsizip/{path}/{inner[0]}"
    return path


def lgn_to_bowen_and_veg(lgn_path: str, extent, out_dir: str, tmp_dir: str, paved=PAVED_CLASSES):
    """Warp LGN (5 m classes) to the 1 m grid with nearest neighbour, then classify."""
    lgn_1m = warp(open_lgn(lgn_path), os.path.join(tmp_dir, "lgn-1m.tif"), extent, 1.0, "near",
                  src_srs=RD, dst_type=gdal.GDT_Int16, dst_nodata=-1)
    ds = gdal.Open(lgn_1m)
    classes = ds.GetRasterBand(1).ReadAsArray()
    nodata = classes == -1
    is_paved = np.isin(classes, paved)

    bowen = np.where(is_paved, BOWEN_PAVED, BOWEN_OTHER).astype(np.float32)
    veg = np.where(is_paved, 0.0, 1.0).astype(np.float32)
    bowen[nodata] = -9999.0
    veg[nodata] = -9999.0

    paths = {}
    for name, arr in (("br-reproject.tif", bowen), ("vegetation-reproject.tif", veg)):
        p = os.path.join(out_dir, name)
        out = gdal.GetDriverByName("GTiff").Create(
            p, ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Float32, GTIFF_OPTS)
        out.SetGeoTransform(ds.GetGeoTransform())
        out.SetProjection(ds.GetProjection())
        b = out.GetRasterBand(1)
        b.SetNoDataValue(-9999.0)
        b.WriteArray(arr)
        out = None
        paths[name] = p
    ds = None
    return paths


# ── Wind placeholder grid ───────────────────────────────────────────────────
def reference_u12(reference_dir: str | None) -> float | None:
    if not reference_dir:
        return None
    path = os.path.join(reference_dir, "json", WIND_FILE)
    if not os.path.exists(path):
        return None
    ds = ogr.Open(path)
    layer = ds.GetLayer()
    names = [layer.GetLayerDefn().GetFieldDefn(i).GetName() for i in range(layer.GetLayerDefn().GetFieldCount())]
    field = next((f for f in ("u_1_2", "u_1.2") if f in names), None)
    if not field:
        return None
    vals = [feat.GetField(field) for feat in layer]
    vals = [v for v in vals if v is not None]
    return float(np.mean(vals)) if vals else None


def write_wind_grid(extent, u12: float, path: str) -> int:
    xmin, ymin, xmax, ymax = extent
    ncols = int(round((xmax - xmin) / CELL_W))
    nrows = int(round((ymax - ymin) / CELL_H))
    col0 = int(round((xmin - GRID_ORIGIN[0]) / CELL_W))
    row0 = int(round((GRID_ORIGIN[1] - ymax) / CELL_H))
    features = []
    for r in range(nrows):
        top = ymax - r * CELL_H
        for c in range(ncols):
            left = xmin + c * CELL_W
            ring = [[left, top], [left + CELL_W, top], [left + CELL_W, top - CELL_H],
                    [left, top - CELL_H], [left, top]]
            features.append({
                "type": "Feature",
                "properties": {
                    "id": len(features) + 1,
                    "grid_col": c,
                    "grid_row": r,
                    # Position on the shared lattice (Middelburg's top-left cell = 0,0)
                    "lattice_col": col0 + c,
                    "lattice_row": row0 + r,
                    "u_1_2": u12,
                    "wind_placeholder": True,
                },
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            })
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "type": "FeatureCollection",
            "name": "wind_reduction",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::28992"}},
            "features": features,
        }, f)
    return len(features)


# ── QGIS project copy ───────────────────────────────────────────────────────
def copy_server_folder(reference_dir: str | None, out_dir: str) -> list[str]:
    """Copy QGIS projects/styles (not sessions) and list the datasources they use."""
    notes = []
    dst = os.path.join(out_dir, "server")
    os.makedirs(os.path.join(dst, "sessions"), exist_ok=True)
    if not reference_dir:
        notes.append("No --reference given: copy data/server/*.qgz and styles into server/ yourself.")
        return notes
    src = os.path.join(reference_dir, "server")
    if not os.path.isdir(src):
        notes.append(f"{src} not found: copy the QGIS project files into server/ yourself.")
        return notes
    for entry in os.listdir(src):
        if entry == "sessions":
            continue
        s, d = os.path.join(src, entry), os.path.join(dst, entry)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
    for entry in sorted(os.listdir(dst)):
        if entry.endswith(".qgz"):
            with zipfile.ZipFile(os.path.join(dst, entry)) as z:
                qgs = next((n for n in z.namelist() if n.endswith(".qgs")), None)
                text = z.read(qgs).decode("utf-8", "replace") if qgs else ""
            sources = re.findall(r"<datasource>([^<]+)</datasource>", text)
            names = re.findall(r"<layername>([^<]+)</layername>", text)
            notes.append(f"{entry} layers -> sources:")
            for n, s in zip(names, sources):
                notes.append(f"    {n}: {s}")
            if "<WMSExtent" in text:
                notes.append(f"    {entry} has a WMS extent restriction; widen it in QGIS if Vlissingen is cut off.")
    return notes


# ── Report ──────────────────────────────────────────────────────────────────
def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def raster_info(path: str) -> dict:
    ds = gdal.Open(path)
    gt = ds.GetGeoTransform()
    return {
        "size_bytes": os.path.getsize(path),
        "width": ds.RasterXSize, "height": ds.RasterYSize,
        "pixel": [gt[1], -gt[5]],
        "extent": [gt[0], gt[3] + gt[5] * ds.RasterYSize, gt[0] + gt[1] * ds.RasterXSize, gt[3]],
        "nodata": ds.GetRasterBand(1).GetNoDataValue(),
        "valid_fraction": round(coverage_fraction(path), 4),
        "sha256": sha256(path),
    }


def check_alignment(out_dir: str, extent) -> list[str]:
    problems = []
    one_m = ["raster/svf-reproject.tif", "raster/br-reproject.tif", "raster/vegetation-reproject.tif"]
    ref = None
    for rel in one_m:
        ds = gdal.Open(os.path.join(out_dir, rel))
        key = (ds.GetGeoTransform(), ds.RasterXSize, ds.RasterYSize)
        if ref is None:
            ref = (rel, key)
        elif key != ref[1]:
            problems.append(f"{rel} grid differs from {ref[0]}: {key} vs {ref[1]}")
    for rel in ["dsm.TIF"] + one_m:
        info = raster_info(os.path.join(out_dir, rel))
        if any(abs(a - b) > 1e-6 for a, b in zip(info["extent"], extent)):
            problems.append(f"{rel} extent {info['extent']} != {list(extent)}")
    return problems


# ── Commands ────────────────────────────────────────────────────────────────
def cmd_build(args) -> None:
    area = dict(AREAS[args.area])
    if args.bbox:
        area["extent"] = snap_to_lattice(*args.bbox)
    extent = tuple(area["extent"])
    if args.area != "middelburg" or args.bbox:
        check_on_lattice(extent)

    out = os.path.abspath(args.out)
    cache = os.path.join(out, "_cache")
    raster_dir = os.path.join(out, "raster")
    for d in ("raster", "json", "uhi", "pet", "shadow-maps", "wind", "_cache"):
        os.makedirs(os.path.join(out, d), exist_ok=True)

    w, h = extent[2] - extent[0], extent[3] - extent[1]
    log(f"Area {area['name']}: extent {extent} ({w/1000:.2f} x {h/1000:.2f} km, "
        f"{int(w/CELL_W)} x {int(h/CELL_H)} wind cells)")

    steps = set(args.only.split(",")) if args.only else {"dsm", "svf", "lgn", "wind", "server"}

    if "dsm" in steps:
        log("1/5 DSM from AHN (0.5 m)")
        tiles = fetch_ahn("dsm_05m", extent, os.path.join(cache, "ahn"))
        warp(tiles, os.path.join(out, "dsm.TIF"), extent, 0.5, "near", dst_type=gdal.GDT_Float32)
        if args.with_dtm:
            log("    + DTM for the wind team")
            tiles = fetch_ahn("dtm_05m", extent, os.path.join(cache, "ahn"))
            warp(tiles, os.path.join(out, "wind", "DTM-0.5.tiff"), extent, 0.5, "near", dst_type=gdal.GDT_Float32)
            shutil.copy2(os.path.join(out, "dsm.TIF"), os.path.join(out, "wind", "DSM-0.5.tiff"))

    if "svf" in steps:
        log("2/5 Sky view factor from KNMI SVF_NL v3 -> 1 m (bilinear)")
        sheets = args.svf_sheets.split(",") if args.svf_sheets else area["svf_sheets"]
        svf_sources = args.svf_files or fetch_svf(sheets, os.path.join(cache, "svf"))
        warp(svf_sources, os.path.join(raster_dir, "svf-reproject.tif"), extent, 1.0, "bilinear",
             src_srs=RD, dst_type=gdal.GDT_Float32)

    if "lgn" in steps:
        if not args.lgn:
            raise SystemExit("--lgn is required for the Bowen/vegetation step (or leave 'lgn' out of --only)")
        log("3/5 Bowen ratio + vegetation from LGN2018 -> 1 m (nearest)")
        lgn_to_bowen_and_veg(args.lgn, extent, raster_dir, cache)

    u12 = args.u12
    source = "--u12"
    if u12 is None:
        u12 = reference_u12(args.reference)
        source = "mean u_1_2 of reference wind grid"
    if u12 is None:
        u12, source = DEFAULT_U12, "default"
    if "wind" in steps:
        log(f"4/5 Placeholder wind grid, u_1_2 = {u12:.4f} ({source})")
        n = write_wind_grid(extent, u12, os.path.join(out, "json", WIND_FILE))
        log(f"    {n} cells")

    notes = []
    if "server" in steps:
        log("5/5 QGIS project files")
        notes = copy_server_folder(args.reference, out)

    with open(os.path.join(out, "area.json"), "w") as f:
        json.dump({
            "name": area["name"], "lat": area["lat"], "lon": area["lon"],
            "extent_rd": list(extent), "wind_placeholder_u_1_2": u12,
        }, f, indent=2)

    report = {"area": area["name"], "extent_rd": list(extent),
              "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "wind_placeholder": {"u_1_2": u12, "source": source}, "files": {}}
    for rel in ("dsm.TIF", "raster/svf-reproject.tif", "raster/br-reproject.tif",
                "raster/vegetation-reproject.tif"):
        p = os.path.join(out, rel)
        if os.path.exists(p):
            report["files"][rel] = raster_info(p)
    have_all = len(report["files"]) == 4
    report["alignment_problems"] = check_alignment(out, extent) if have_all else ["not all rasters built yet"]
    report["notes"] = notes
    with open(os.path.join(out, "prepare-report.json"), "w") as f:
        json.dump(report, f, indent=2)

    log("Done. Summary:")
    for rel, info in report["files"].items():
        log(f"    {rel:34s} {info['width']}x{info['height']} px, "
            f"{info['size_bytes']/1e6:7.1f} MB, valid {info['valid_fraction']*100:5.1f}%")
    for p in report["alignment_problems"]:
        log(f"  ! {p}")
    for n in notes:
        log(f"  {n}")
    log(f"Report: {os.path.join(out, 'prepare-report.json')}")
    log("You can delete _cache/ once you're happy with the result.")


def cmd_verify(args) -> None:
    """Derive Bowen/vegetation for Middelburg and compare with the existing hand-made files."""
    ref = os.path.abspath(args.reference)
    br_ref = os.path.join(ref, "raster", "br-reproject.tif")
    veg_ref = os.path.join(ref, "raster", "vegetation-reproject.tif")
    ds = gdal.Open(br_ref)
    gt = ds.GetGeoTransform()
    extent = (gt[0], gt[3] + gt[5] * ds.RasterYSize, gt[0] + gt[1] * ds.RasterXSize, gt[3])
    res = gt[1]
    ds = None
    log(f"Reference grid {extent}, {res} m")

    tmp = os.path.join(args.tmp, "verify")
    os.makedirs(tmp, exist_ok=True)
    lgn_res = warp(open_lgn(args.lgn), os.path.join(tmp, "lgn.tif"), extent, res, "near",
                   src_srs=RD, dst_type=gdal.GDT_Int16, dst_nodata=-1)
    lgn_ds = gdal.Open(lgn_res)
    classes = lgn_ds.GetRasterBand(1).ReadAsArray()
    lgn_ds = None
    paved = np.isin(classes, PAVED_CLASSES)

    for label, path, expect in (
        ("Bowen", br_ref, np.where(paved, BOWEN_PAVED, BOWEN_OTHER)),
        ("Vegetation", veg_ref, np.where(paved, 0.0, 1.0)),
    ):
        rds = gdal.Open(path)
        band = rds.GetRasterBand(1)
        have = band.ReadAsArray().astype(np.float64)
        nd = band.GetNoDataValue()
        if have.shape != expect.shape:
            log(f"{label}: shape {have.shape} vs derived {expect.shape} — grids differ, can't compare 1:1")
            continue
        valid = np.isfinite(have) & (classes != -1)
        if nd is not None:
            valid &= ~np.isclose(have, nd)
        agree = np.isclose(have, expect) & valid
        pct = 100 * agree.sum() / max(valid.sum(), 1)
        log(f"{label}: {pct:.2f}% of {valid.sum():,} pixels match the LGN rule "
            f"(file mean {have[valid].mean():.3f}, derived mean {expect[valid].mean():.3f})")
        vals, counts = np.unique(np.round(have[valid], 3), return_counts=True)
        top = sorted(zip(counts, vals), reverse=True)[:6]
        log(f"    most common values in file: " + ", ".join(f"{v:g} ({c/valid.sum()*100:.1f}%)" for c, v in top))
        if pct < 95:
            mism = classes[valid & ~agree]
            cv, cc = np.unique(mism, return_counts=True)
            worst = sorted(zip(cc, cv), reverse=True)[:8]
            log("    LGN classes where they disagree: " + ", ".join(f"{int(v)} ({c:,} px)" for c, v in worst))
    log("Above ~95% means the scripted rule reproduces the Middelburg files; "
        "otherwise ask Valeria how they were made before trusting the Vlissingen versions.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Build a data folder for an area")
    b.add_argument("--area", choices=sorted(AREAS), default="vlissingen")
    b.add_argument("--bbox", type=float, nargs=4, metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                   help="Custom RD extent; snapped outward to the wind-grid lattice")
    b.add_argument("--out", required=True, help="Output data folder, e.g. /out")
    b.add_argument("--lgn", help="LGN2018 raster (.tif/.img or the .zip from lgn.nl)")
    b.add_argument("--reference", help="Existing Middelburg data folder (for wind placeholder + QGIS project)")
    b.add_argument("--u12", type=float, help="Placeholder u_1_2 for every wind cell")
    b.add_argument("--svf-sheets", help="Comma-separated AHN sheet ids for KNMI SVF, e.g. 65cz2,65dz1")
    b.add_argument("--svf-files", nargs="+", help="Use already-downloaded SVF GeoTIFFs instead of the KNMI API")
    b.add_argument("--with-dtm", action="store_true", help="Also fetch the DTM and copy DSM/DTM into wind/")
    b.add_argument("--only", help="Comma-separated subset of steps: dsm,svf,lgn,wind,server")
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("verify-middelburg", help="Check the LGN rule against existing Bowen/vegetation files")
    v.add_argument("--lgn", required=True)
    v.add_argument("--reference", required=True, help="Middelburg data folder (contains raster/)")
    v.add_argument("--tmp", default="/tmp", help="Scratch folder")
    v.set_defaults(func=cmd_verify)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
