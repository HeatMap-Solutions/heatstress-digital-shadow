# Zeeland proof of concept within a 100 GB hosting disk

The hosting allowance is 100,000,000,000 bytes, including application software,
data, temporary calculation space and free headroom. The profile uses 5 m PET,
SVF and thematic rasters, plus 5 m DSM/DTM and wind masks. The 140 x 280 m wind
cells and existing weather/PET equations stay unchanged. Continuous source
rasters use average resampling; LGN categories use nearest neighbour. This
changes input detail and requires a comparison, not an assumed accuracy claim.

| Allocation | Decimal GB |
|---|---:|
| Operating system, application/container images and bounded logs | 20 |
| Prepared inputs and published map | 20 |
| Retained scenario versions | 20 |
| Temporary generation workspace | 20 |
| Free-space reserve | 20 |
| Total | 100 |

Original AHN/SVF/LGN archives and previous 1 m experiments stay on the developer
machine or a separate archive. They are not part of the hosting copy. The host
keeps the prepared 5 m inputs needed by PET/scenarios, and source provenance.
A 100 GB host is not a 100 GB limit for all historical development copies.

`storage-budget.json` limits the entire generated regional directory to 60 GB,
and its runtime `app-data` directory to 40 GB. Preparation/generation check
capacity before admitting a stage. Scenario updates share a storage-admission lock on the bounded dataset, so
concurrent sessions cannot independently spend the same remaining allowance.
Updates include space for a new
full PET revision and private working rasters. A rejected request leaves the
published project unchanged. No user scenario or original dataset is silently
deleted. Archive unwanted scenarios explicitly when the allowance is reached.
The minimum free-space reserve is 20 GB. Container logs rotate at 10 MB with
three files per service. Limits cover application data; unrelated host software,
external backups and filesystem overhead require their own accounting.

## Generate

Use the installed QGIS image, mount original `/data` read-only and mount only
`/data/zeeland/zeeland-poc-5m` writable. Also mount this checkout's `qgis` as
`/app`. Run with these environment settings:

```text
REGION_ROOT=/data/zeeland/zeeland-poc-5m
PET_RESOLUTION_M=5
QT_QPA_PLATFORM=offscreen
QGIS_PREFIX_PATH=/usr
PYTHONPATH=/usr/share/qgis/python
```

Run `python3 /app/tools/prepare_walcheren.py` then
`python3 /app/tools/generate_walcheren.py`. The retained tool names preserve the
existing Walcheren workflow; REGION_ROOT selects the separate Zeeland dataset.
The official provincial PV29 polygon is `boundary/region-rd.gpkg`; its original
source record is retained in `boundary/source.json`. The preparation audit
processes bounded rows rather than allocating the province as a NumPy array.
For coarse height preparation, each recorded archive is extracted sequentially
into a private temporary directory, averaged once, and discarded there. Two
independent GDAL workers retain at most two extracted tiles concurrently. Only
the small coarse tile is retained. This avoids repeated random seeks inside
compressed ZIP files and keeps peak extraction to two source tiles. A regression
compares all output pixels with direct GDAL average resampling.
Outside-polygon/water/unsupported cells remain NoData. Generation records pixel
counts and square kilometres using the actual output resolution.

The filled SVF and local PET search distance remains 10 metres: two pixels at
5 m, rather than accidentally expanding the interpolation to 50 metres. Wind
preparation also retains its existing 10 metre DTM fill distance. Coarse heights
can lose small obstacles; fallback pedestrian wind remains a provisional 7.5
m/s. Fixed temperature/weather and mixed historic dates remain experimental.

## Preview and deploy

Set `ZEELAND_DATA_PATH` to the generated `app-data` absolute path. Then run:

```bash
docker compose -f compose.zeeland-poc.yml up -d
```

The separate local preview uses frontend 15174, backend 18001, QGIS 19001 and
WMS 18011. It labels the map as Zeeland/5 m and opens the provincial extent.
It preserves the existing Walcheren preview. The compose file binds localhost;
external hosting/authentication deployment is a separate operational step.

For hosting, copy only the coarse regional output and application files. Do not
copy the parent `data/zeeland` directory or development `node_modules`, Git
history, old sessions or source archives. Measure the final deployment directory
and Docker image sizes, and confirm the 20 GB free reserve before deployment.

## Verification

`tests/test_coarse_region.py` checks real 5 m height processing, polygon/water
masking and area units, exact 3x3 versus full scenario agreement, deletion
restoration, and storage admission. Run it with the existing baseline/local/PET
suites in the QGIS runtime. Test results, measured dataset sizes and full-map
coverage must be recorded before claiming a completed provincial proof.

## Local comparison evidence, 14 October

The final combined GIS suite passed 38 cases (26 existing writer deprecation
warnings). Frontend tests passed 27 cases; lint and both default/Zeeland builds
passed in the installed Docker runtime. Host node tooling was unavailable, so
its initial failed commands are not counted as passing checks.

A real 700 x 1400 m Middelburg wind window used 49.68 MB of working files at
legacy resolution and 6.11 MB with 5 m heights/masks. Illustrative times were
55.65 s and 26.51 s with provincial preparation active. Across 25 paired cells,
An initial comparison showed raw wind differences of 0.3030 mean and 0.4294
maximum. That exposed a separate pixel-count unit bug: the existing frontal
width formula assumed each raster pixel represented one square metre. The
coarse calculation now converts pixel counts into actual square metres; the
1 m formula is unchanged. The initial comparison and partial wind run are
retained as rejected evidence; final corrected differences must be recorded. These
differences have no agreed scientific tolerance; coarse wind is explicitly an
experimental input and storage/run feasibility remains separate from accuracy.

Corrected real comparison: mean absolute raw wind difference 0.031319, maximum 0.086902. All 25 original 1 m raw wind values remain exactly unchanged. Coarse detail still needs scientific acceptance.

The local provincial run hit Docker's memory limit after all 552 wind windows
were saved (360 calculated, others skipped). Docker confirmed an OOM event.
A fresh process reused these outputs and completed assembly in 72.06 s. This
is resumed assembly time, not the total wind calculation time. A cold run on
the current 8.2 GB Docker allocation is not yet proven unattended; checkpoint
recovery is part of the observed workflow. The 100 GB disk allowance remains
a separate constraint from RAM.

## Completed provincial verification, 14 October

The resumed generation completed successfully (exit 0, no OOM in the resumed
process). The published raster is 12908 x 12824 pixels, EPSG:28992, with square
5 m pixels and NoData -9999. PET covers 1760.003625 km2, or 99.49% of the
1769.02205 km2 identified as LGN land. About 9.02 km2 of identified land remains
unsupported. An independent 128-row audit checked all 165532192 pixels:
zero valid pixels outside the province polygon, zero valid pixels on LGN water
classes 16/17, and zero disagreements between PET validity and the support mask.
Of supported pixels, 90.64% use the provisional pedestrian-wind fallback.
This coverage demonstrates processing feasibility; scientific validity remains
unproved. The zonal PET stage took 4929.34 s in the resumed run.

Before test scenarios, the hosting `app-data` occupied 1.79 GB allocated
(1.74 GB logical). The complete generation directory occupied 3.74 GB allocated
(3.54 GB logical). After verification retained two scenario raster revisions,
these were 2.10 GB and 4.06 GB allocated respectively. The five application
images have a conservative summed image size of 8.43 GB; shared Docker layers
can reduce actual use. Approximately 10.5 GB covers these image sizes plus
the tested hosting data. OS, dependencies, logs, future scenarios and temporary
files still need the remaining allocations in the 100 GB budget. This is a local
measurement, not an external hosting deployment or a guarantee for unlimited
retained scenarios. Original archives remain outside the hosting copy.

The separate localhost preview is running at http://127.0.0.1:15174/.
All eight WMS overlays returned HTTP 200 PNGs and their legends returned data.
Chrome rendered the provincial PET map, the 5 m label and the diagnostic panel;
no browser error messages were reported during inspection. A real API request
added a 15 m tall, 5 m radius tree near central Middelburg in 7.38 s and selected
nine grid cells. Two PET pixels changed, none outside those cells, and the
validity mask stayed unchanged across the complete raster. Repeating the same
request returned no changes in 0.04 s. Removing the tree took 6.85 s and
restored every pixel to the baseline exactly. These are warm local observations
with other stacks running, not controlled performance benchmarks or proof that
every scenario geometry has acceptable scientific effects.

Evidence is retained locally in `.agent-notes/evidence/zeeland-live-verification.json`,
`zeeland-polygon-verification.json`, and `zeeland-frontend.png`. Changes remain
uncommitted; no public deployment, push, PR or wiki publication was performed.
