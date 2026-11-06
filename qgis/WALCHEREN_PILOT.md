# Walcheren generation pilot

This branch generates a base PET raster across the union of Middelburg, Veere
and Vlissingen, then uses the existing 3x3 scenario update implementation.
The developer agreed this boundary, the existing fixed weather and PET formulas,
and a provisional 7.5 m/s fallback where morphology wind is unavailable.

The fallback is **pedestrian wind consumed by PET**, applied after the existing
weather scaling. It is an experiment assumption, not a measurement or a validated
conversion from ten-metre station wind. Available morphology wind still follows
the existing equations. The output records fallback cells. These results must
not be presented as a scientifically validated regional map.

## Inputs and calculation

- Boundary: official Kadaster/PDOK `gemeentegebied`, GM0687, GM0717 and GM0718,
  retrieved 13 October 2026. Administrative geometry includes extensive water.
- AHN3 DSM and DTM: retained native 0.5 m ZIP members, accessed through the
  recorded VRTs. Only the Walcheren working rectangle is prepared; the province
  archives are not fully extracted or changed.
- SVF: all 119 recorded `SVF_NL/3` manifest paths, including three reused tiles
  in earlier acquisition folders, resampled with nearest neighbour. The first
  draft scanned a single folder and omitted those tiles, including Middelburg.
  Revision 2 corrects that preparation bug; the previous output is retained.
- Land use: LGN2018 at native 5 m. Classes 18, 19, 24, 25, 26 and 27 give Bowen
  3; other valid classes give 0.4. The inverse paved classification supplies the
  existing vegetation proxy. Resampling to 1 m does not add source detail.
- Geometry: cached BAG and BGT in EPSG:28992. BAG excludes demolished,
  unrealised and incorrectly registered statuses, matching the saved legacy WFS
  count. The BGT point selection follows the existing automated service.
  Current vectors and historical AHN3 describe different acquisition periods.

The PET grid is 1 m, north-up EPSG:28992. Wind cells retain 140 x 280 m and
the original Middelburg lattice; regional row and column indices start at zero.
Wind uses sequential 2800 x 2800 m windows, with one surrounding cell of
preprocessing context, retaining only each window's core. Existing urban/rural
halo evidence motivates that margin; it does not establish coastal seam accuracy.

The fixed inputs remain: ff10 7.677, minimum/maximum temperature 27.2/29.1,
average wind 7.5, base temperature 28.3, relative humidity 44, phi 44,
global radiation 663 and diffuse radiation 0.2. UHI uses 1 July 2017 at 18:00;
hillshade uses 1 July 2015 at 15:00, latitude 51.498 and longitude 3.613.
The mixed dates and fixed sun location reproduce existing assumptions. Browser
solar controls do not regenerate this base map's weather.

Water (LGN classes 16 and 17), outside-polygon pixels and unresolved required
source pixels remain NoData. The final support raster constrains subsequent
updates. An object centre outside supported land is rejected; crowns and
processing halos can cross the boundary, but published water stays NoData.

## Run and inspect

The tools run in the installed QGIS image. They expect immutable sources at
`/data` and a writable `/data/zeeland/walcheren-pilot` directory. From this
worktree, replace the two source-directory placeholders with the original
checkout's absolute paths:

```bash
docker run --rm --platform linux/amd64 --network none \
  -e QT_QPA_PLATFORM=offscreen -e PYTHONPATH=/usr/share/qgis/python \
  -e QGIS_PREFIX_PATH=/usr -e QGIS_DEBUG=0 -e CPL_TMPDIR=/tmp \
  -v "$PWD/qgis:/app:ro" \
  -v /absolute/path/original/data:/data:ro \
  -v /absolute/path/original/data/zeeland/walcheren-pilot:/data/zeeland/walcheren-pilot:rw \
  -w /tmp heatstress-digital-shadow-qgis \
  python3 /app/tools/prepare_walcheren.py
```

Set `WALCHEREN_ROOT=/data/zeeland/walcheren-pilot-v2` in the container to prepare
a separate corrected revision. The default remains the original output root;
a stale SVF mosaic now fails validation rather than silently retaining gaps.

Run the same container command with `generate_walcheren.py` after preparation
finishes. It checkpoints wind windows and completed stages. Review a changed
script or changed source configuration before resuming an existing run.

Set `WALCHEREN_DATA_PATH` in the worktree's ignored root `.env` to the absolute
path of the pilot's `app-data` directory. Compose requires that path explicitly;
the checkout's existing tracked `data` directory is not the pilot dataset.
Another clone needs its own retained/generated pilot data and matching path.

```bash
docker compose -f compose.walcheren.yml up -d
```

The preview uses port 15173 for the UI, 18000 for the backend, 19000 for QGIS,
and 18010 for WMS. It uses its own Compose project and sessions. The wind debug
UI uses the preview's same-origin proxy. Base-map generation uses the CLI above;
the legacy `/pet/full-map-generation` endpoint remains the original prepared
Middelburg workflow and is not the regional generator.

Evidence lives in `data/zeeland/walcheren-pilot/`: `boundary/source.json`,
`prepared/manifest.json`, `generation.json`, individual wind grids and
`app-data/server/pet-version-1.tif`. The generation manifest records fixed weather,
runtime, stage timings, fallback counts, supported pixel counts and the final
PET hash. Preparation time is separate from base generation and scenario time.

The verified local runtime is QGIS 4.3.0-Master and GDAL 3.10.3, image
`sha256:a1cdb2cb735881a119c7c332685cf89c33d1d3deb026f7bf49d585f69cc9d279`.
The host is Mac14,15 with 16 GiB RAM and eight CPUs; Docker has eight CPUs and
8,218,251,264 bytes memory. Generation sets a 256 MiB GDAL cache, one GDAL thread
and a 10 GiB free-space reserve. No performance acceptance target is agreed.

## Verification

Run the offline calculation regressions in the QGIS image:

```bash
python3 -m pytest tests/test_local_pet.py tests/test_pet_baseline.py \
  tests/test_shadow_pipeline.py tests/test_walcheren_support.py \
  tests/test_walcheren_generation.py tests/test_walcheren_preparation.py -q -p no:cacheprovider
```

The support tests check that patches preserve water NoData and unsupported
objects cannot publish. The generation test checks that fallback wind is exactly
7.5 after scaling and that missing zonal inputs are excluded. Existing baseline
tests compare independent original-main reference calculations in their recorded
fixtures. None establishes scientific accuracy over all Walcheren.

A confirmed storage bottleneck was also corrected: the raster calculator reads
windows from its first input, while filled SVF used full-width row strips.
Column-first tile traversal repeatedly read wide strips and wrote partial output
strips. The filled SVF was retiled and every pixel byte, grid property and NoData
value compared before replacement. Regional sun, shade and combined PET outputs
now use tiled, lossless compression. An opt-in shade storage regression compares
all values and the unchanged legacy grid. The completed sun stage took 118.48 s;
the earlier interrupted attempt is not a completed timing reference.

Two failed draft approaches were corrected before full generation: a temporary
QGIS raster object's lifetime caused a native crash during zonal statistics;
editing new zonal fields in GeoJSON produced invalid field indices. Calculation
now retains raster objects and uses a private memory layer plus GeoPackage.

Before calling the pilot ready, inspect complete output coverage and grid/NoData
properties, query its actual WMS values, and verify native browser tree add,
save, reload and deletion on the isolated map. Agree scientific tolerances,
coastal seam validation and a defensible rural wind method separately.

## Regional diagnostic layers

The layer picker includes PET, Updated PET, pedestrian wind consumed by PET,
raw morphology wind, DSM elevation, filled SVF, Bowen ratio and the LGN vegetation
proxy. The proxy is not NDVI. Raw morphology wind is NULL where the 7.5 m/s
assumption supplies the pedestrian input. Diagnostic raster IDs retain the
legacy API names, but all datasources are regional. Raster colour limits are
fixed for inspection and do not change calculation values.

Copied session projects resolve both sibling and parent-directory sources,
including GeoPackage layer options. Verify that relocation with the added
backend regression, run from the worktree:

```bash
docker compose -f compose.walcheren.yml exec -T backend \
  python3 -m unittest discover -s tests -v
```

Rejected scenario Saves display the server's reason and retain the unsaved draft.
Retry and persistence are covered by the frontend hook regression.

## Corrected publication and verification, 14 October 2026

The corrected data revision is `data/zeeland/walcheren-pilot-v2`. Set
`WALCHEREN_DATA_PATH` to its `app-data` directory for the preview compose file.
Preparation reads all 119 SVF manifest paths, including three reused acquisition
files omitted by the first run. Existing Middelburg source coverage is restored.
PET supports 208.845482 km2 of 214.452037 km2 LGN land (97.386%); unresolved
5.606555 km2 stays masked. The earlier output is retained separately.

Verification passed: 31 GIS tests, 27 frontend tests, frontend lint/build,
five backend tests, and GetMap/GetFeatureInfo/legend for all seven base layers.
Live Middelburg PET was 28.7726 at 3.61299629,51.49809061. A native one-tree
Save changed 71 pixels entirely within its nine selected grid cells, with no
NoData-mask changes across the full raster. An independent warm API run took
22.98 s; raster copying consumed 9.72 s. These are local engineering checks,
not scientific validation or a controlled cold-cache regional benchmark.

The map server exited with SIGSEGV during final browser verification. Recovery
uses serial rendering and automatic restart; the cause remains unresolved.
A separate missing-cookie fallback was corrected and live GetMap returns PNG.
Final native reload/delete verification remains incomplete. Changes are local
and uncommitted on `feature/walcheren-pilot`.
