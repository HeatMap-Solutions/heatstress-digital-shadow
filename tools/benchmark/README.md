# Running calculation benchmarks

This tool measures the current application without changing its calculation code,
wind-field names, supplied data, weather constants, or random foliage generation.
It calls the existing APIs in disposable containers and saves evidence for each
attempt. A failed or blocked generation is not a successful generation time.

## Requirements

- Run from this repository with Python 3.9+ and Docker Desktop/Engine available.
- Set up the normal application and supplied `data/` folder using the
  [project setup guide](https://github.com/jrcz-data-science-lab/heatstress-digital-shadow/wiki/1.-Setting-up-the-application).
- Existing containers named `qgis` and `backend` must be present so the tool can
  reuse their exact image IDs and calculation environment/resource settings.
  Normally these are created by `docker compose up -d`. No image download/build
  is performed by the benchmark runner.
- Have several GB free for temporary working data; use `--save-maps` to retain all
  generated files after validation. With that flag, this checkout produced about 1.2 GB
  of artifacts in its first one-tree smoke test, plus temporary working copies.
  The preflight requires at least 6 GiB free or five times input size, whichever
  is larger. Larger/custom scenarios may require more.

No Python packages need to be installed on the host: the runner uses the standard
library. GIS validation uses GDAL/NumPy already installed in the QGIS image.

## Quick start

All commands below run from the repository root. Windows users may use `python`
instead of `python3` if that is their Python executable.

List the scenarios and their measurement boundaries:

```sh
python3 tools/benchmark/bench.py list
```

Run three independent attempts of each tree scenario:

```sh
python3 tools/benchmark/bench.py run one-tree --repeat 3 --label baseline
python3 tools/benchmark/bench.py run ten-trees --repeat 3 --label baseline
```

The commands execute sequentially. Each repetition starts fresh containers and a
fresh session with reset working files. The tool stops repetitions after the first
non-completed attempt, retaining its evidence instead of retrying a known failure.
Three successful repetitions also generate a comparison report automatically.

By default, runs save logs, timing/resource measurements, input fingerprints,
validation summaries and an inventory of generated files. Generated text evidence
(`.json`, `.csv`, `.txt`, `.log`, `.xml`) is retained only up to 1 MiB per file.
Raster maps, vector data, projects and other binary intermediates are not retained.
Maps are still generated and validated in disposable working data before cleanup;
this changes saved evidence, not the calculation or measured request boundary.

To save everything generated, including final and intermediate maps:

```sh
python3 tools/benchmark/bench.py run one-tree --repeat 3 --label baseline --save-maps
```

Use `--save-maps` on both baseline and later runs when you need numerical raster
comparisons. Without saved maps, timing comparisons remain available and raster
comparisons explicitly report unavailable. `--keep-work` separately preserves
all disposable inputs/outputs for debugging and defeats the lightweight disk policy.
Temporary disk-space requirements and the preflight guard still apply.

Check the other existing calculation workflows:

```sh
python3 tools/benchmark/bench.py run pet-generation --label baseline
python3 tools/benchmark/bench.py run shadow-map --repeat 3 --label baseline
python3 tools/benchmark/bench.py run wind-generation --label baseline
```

Open the printed `report.html` path in a browser. Reports are self-contained and
work offline. Browser Print / Save as PDF can be used for meetings. Relative
links to raw evidence work when the surrounding run folders are retained.

After an application change, repeat the same scenario on the same machine with a
new label, such as `--label after-change`. Compare the selected saved run IDs:

```sh
python3 tools/benchmark/bench.py compare RUN_ID_1 RUN_ID_2 RUN_ID_3 --name meeting
```

Replace `RUN_ID_1` etc. with the folder names printed by the runner. Full run-folder
paths are also accepted. Put baseline runs first: percentage changes use the first
label in each scenario as the reference. This command also compares corresponding
final rasters using a disposable QGIS image. It records unavailable comparisons
without changing the original evidence.

## Scenario coverage

| Scenario | Existing entry point | Included calculation | Excluded or current limitation |
| --- | --- | --- | --- |
| `pet-generation` | QGIS `GET /pet/full-map-generation` | Wind scaling, UHI/air/wet-bulb calculations, partial/full PET, hillshade and final combination | Starts from prepared maps. Supplied legacy `u_1.2` currently fails the `u_1_2` preflight; no rename is made. |
| `one-tree` | Backend `POST /update-pet` | Existing DSM/Bowen modifications, sun PET, hillshade, combination, filling, session-project update | Reuses existing wind/SVF and partial PET. Does not time browser refresh. |
| `ten-trees` | Same route | Same work, with ten fixed placements | Distributed load case, not one clustered planting scheme. |
| `shadow-map` | QGIS `POST /shadow/map` | Current DSM hillshade at 2015-07-01 15:00 | One time step; separate from whole PET generation. |
| `wind-generation` | QGIS `POST /wind/generate-wind-reduction-map` | Existing independent wind pipeline, including live building/tree downloads | Requires `wind/DSM-0.5.tiff` and `wind/DTM-0.5.tiff`, absent in the inspected data. |

The wind UI also offers individual height/import/rasterisation/aspect/grid
operations. Their work is covered by the wind-generation scenario when its inputs
exist; they are not separately timed by this initial tool. Historical manual
Bowen/NDVI/SVF preparation is not automatically orchestrated here. Missing stages
are not synthesized, and separate measurements must not be added and described as
one complete raw-data-to-map execution.

The tree coordinates were reviewed and approved for this initial benchmark.
See `benchmark/scenarios/tree-locations.png`. One tree uses the first point of
the ten-tree set. All use height 15, radius 5 and circle, matching the current
object preset; coordinates use EPSG:28992. The current code writes the height value
into DSM cells, so these are application preset values, not independently
validated tree heights above local terrain. No physical planting recommendation
is implied.

## What is saved

An **artifact** is a file retained as evidence of an attempt: a measurement,
application output, log, or validation result. Start with `report.html`; the
other files explain where its numbers came from and support later investigation.

```text
benchmark/
  scenarios/                         reusable JSON definitions and placement preview
  runs/<UTC-time>_<scenario>_<id>/
    run.json                         status, identity, inputs, environment, request
    scenario.json                    exact scenario used for this attempt
    preflight.json                   GIS/field checks (when container inspection ran)
    response.json                    actual HTTP result (if a request was submitted)
    report.html                      readable report
    measurements/timings.csv          request timing and excluded tool overhead
    measurements/resources.csv        timestamped Docker CPU/memory samples
    logs/                            application, request-driver and sampler logs
    outputs/                         generated or rewritten data files
    outputs/container-temporary/     remaining temporary files from each container
    artifacts.json                   output paths, sizes and SHA-256 fingerprints
    validation.json                  final raster/project checks
  comparisons/<UTC-time>_<name>_<id>/
    report.html
    comparison.csv
    comparison.json
    raster_differences.json
    <run-id>_difference.json          detailed result for one raster comparison
```

### Run records and measurements

Paths in this table are relative to one run folder.

| File | What it contains and when to use it |
| --- | --- |
| `report.html` | Offline human-readable summary: status, request duration, CPU/memory, measurement boundary, problems, and evidence links. Open this first or print it for a meeting. |
| `run.json` | Main run record: UTC timestamps, scenario and label, status/problems, machine identity, Git revision/branch, source fingerprints, input paths/hashes, Docker images/settings, available software versions, cache/randomness policy, request result, and cleanup/verification information. Use it to establish whether runs are comparable. Fields are filled as stages execute, so early failures have fewer fields. |
| `scenario.json` | Exact saved scenario definition: endpoint, method, payload (including tree coordinates), required inputs, expected outputs, measurement boundary, and exclusions. This snapshot preserves what was requested even if the reusable scenario is edited later. |
| `preflight.json` | Input inspection: raster dimensions, CRS and grid transform; required wind field; placement footprint checks where applicable; installed GIS versions. Exists only if container-side inspection ran. Missing-file checks can stop earlier and appear in `run.json` instead. |
| `response.json` | HTTP status, response body or error, elapsed seconds, and request-level outcome. Written when the request driver returns a result; can be absent if execution stops earlier. Its success is provisional until output validation finishes; `run.json` gives the final status. |
| `measurements/timings.csv` | `metric`, `seconds`, `status`. `request` is the timed HTTP exchange. `preparation` and `entire_tool_run` are excluded overhead/observation measurements, not additional calculation stages. Blank seconds mean unavailable. |
| `measurements/resources.csv` | One row per Docker observation: `utc` timestamp, `observation_seconds` since sampler startup, `service`, `cpu_percent`, `memory_bytes`. These produce the report's resource summaries. A header-only file means no samples, not zero usage. |
| `validation.json` | Expected-output checks and final raster summaries: readability, dimensions, CRS, grid, valid/NoData counts and value range. For tree runs it also checks that the updated project layer references the filled PET output. Initially contains `Not executed`; a blocked run may retain that placeholder. This is structural validation, not a scientific accuracy verdict. |
| `artifacts.json` | Inventory of generated/rewritten files: path, byte size, SHA-256 content fingerprint and `retained` flag. Paths with `retained: false` are not saved. Use it to locate outputs and verify file identity. An empty list means no generated files were collected. It inventories generated output candidates, not every report or log. `run.json` records generated bytes separately from retained `artifact_bytes`. Older manifests without `retained` describe saved files. |

SHA-256 is a content fingerprint: matching fingerprints indicate matching file
bytes. It does not describe scientific correctness. CRS means coordinate reference
system; NoData marks cells with no usable value, rather than a temperature of zero.

### Logs

| File | Purpose |
| --- | --- |
| `logs/qgis.log` | QGIS calculation container output, including processing messages and application errors. Use it to investigate failed or unexpected calculations. |
| `logs/backend.log` | Backend container output, including session/request handling and errors communicating with QGIS. |
| `logs/request-driver.log` | Standard output/error from the benchmark's HTTP driver. Usually quiet on success; useful if `response.json` was not produced. |
| `logs/docker-stats.raw` | Original Docker resource-statistics stream before conversion to CSV. Preserves raw observations for auditing parser behavior. |
| `logs/resource-sampler.log` | Diagnostic/error output from the Docker statistics command. Empty can be normal. |

Logs appear only when the corresponding process ran and its output was collected.
They are diagnostic evidence, not a reliable source of individual stage timings.

### Generated map files and temporary artifacts

With `--save-maps`, `outputs/` mirrors paths beneath the disposable `/data` directory. For example,
an application response pointing at `/data/server/sessions/.../pet_....tif`
corresponds to `outputs/server/sessions/.../pet_....tif` in the saved run.
The filenames below describe observed tree/shadow runs and currently declared
generation outputs. Failed or blocked scenarios do not necessarily produce them.

| Path or filename pattern | Meaning |
| --- | --- |
| `outputs/server/sessions/<session-id>/dsm_<time>.tif` | Surface-height raster after inserting the approved tree shapes/foliage. Used to calculate their shadows. |
| `.../bowen_<time>.tif` | Bowen-ratio raster after the app applies its existing tree-area value. Used in sun PET recalculation. |
| `.../sun_pet_<time>.tif` | Recalculated PET for sun conditions, before combining with shadow PET. |
| `.../pet_<time>.tif` | Combined PET before filling NoData gaps. The current tree API returns this path. |
| `.../pet_<time>_filled.tif` | PET after the app's gap-filling step. **This is the final tree-scenario raster used for validation and numerical comparisons.** |
| `.../map.qgz` | Compressed QGIS session project with the updated PET layer pointing to the filled raster. It stores layer configuration/references; it does not embed all source datasets and is not guaranteed to open standalone elsewhere. |
| `outputs/shadow-maps/hillshade_20150701_1500.tif` | Shadow/hillshade result for the app's simulated 2015-07-01 15:00 time. Produced from the modified DSM for tree runs or the supplied DSM for standalone shadow runs. It is not a temperature map. |
| `outputs/shadow-maps/shadow_map_aligned.tif` | Intermediate shadow raster produced by the existing alignment helper before PET combination. Its name does not guarantee that its grid exactly matches the PET raster. |
| `outputs/pet/pet.tif` | Expected final raster for the separate PET-generation scenario. Today's blocked generation attempt did not produce it. |
| `outputs/wind/benchmark-output/run_*/height.tif`, `wind-grid.gpkg`, `run-settings.json` | Expected height raster, wind-grid vector data, and parameters from the independent wind-generation pipeline. Today's missing-input attempt did not execute this pipeline; other intermediates would also be retained if generated. |
| `*.aux.xml` | GDAL sidecar metadata, which can contain statistics and other raster properties. A rewritten sidecar may be captured even when the associated source raster's pixels were unchanged. |
| `*.ovr`, `*.msk` | Raster overview/pyramid and validity-mask sidecars, respectively, if present or generated. Required existing sidecars are copied with inputs; unchanged ones are not repeated as outputs. |
| `outputs/container-temporary/qgis/leaf_clouds.gpkg` | Retained vector points used by the existing random-foliage calculation. Useful for investigating why identical tree requests can yield different maps. |
| `outputs/container-temporary/qgis/buffered_point.gpkg` | Retained vector geometry from buffering placed points during raster modification. |
| `outputs/container-temporary/<service>/qt_temp*`, `*_styles.db`, other files | Remaining runtime/QGIS/Qt temporary files. Kept for diagnosis; not final scientific outputs. Names and presence vary between runs. |

GeoTIFF (`.tif`/`.tiff`) stores a geographically located grid of values. GeoPackage
(`.gpkg`) stores GIS data such as points or grid polygons. Open these in GIS
software for investigation; use `artifacts.json` for the exact files actually
generated by a particular attempt. With `--save-maps`, the runner also captures any other new or
rewritten files, so the inventory is authoritative when the app's outputs change.

The optional `.work/` directory contains disposable input copies and container
temporary directories. It is normally removed after collection. `--keep-work`
retains it, and some verification failures can retain it for diagnosis. It is
not an additional successful result.

### Comparison artifacts

These live in their own folder under `benchmark/comparisons/` and reference the
selected runs; they do not modify or replace those runs.

| File | Meaning |
| --- | --- |
| `report.html` | Meeting-oriented summary of medians/ranges per scenario and label, all attempt statuses, resource summaries, coverage gaps, and raster differences. |
| `comparison.csv` | One row per selected attempt: `run_id`, `scenario`, `label`, `status`, `elapsed_seconds`. Failed durations remain visible but are excluded from successful medians in the report. |
| `comparison.json` | Selected run IDs and warnings about incompatible scenario, input, environment, or measurement definitions. |
| `raster_differences.json` | Combined results comparing each later successful run's selected final raster against the first successful run of its scenario. Records unavailable/incompatible comparisons as well. |
| `<run-id>_difference.json` | Individual numerical result when the raster comparison completed: grid compatibility, jointly valid cell count, mean/max absolute difference, and NoData-mask differences. No resampling or accuracy threshold is applied. |

The baseline/reference run does not need a difference file against itself.
Tree PET differences are in °C; shadow differences are in the raster's values.
NoData-mask differences count cells valid in one map but missing in the other.
Average differences use cells valid in both rasters.

## Folder naming and timestamps

Run names already include the creation date **and time to the second**, in UTC:

```text
YYYY-MM-DD_HHMMSS_<scenario>_<8-character-random-id>
2026-09-07_124418_one-tree_6a9320e8
```

The example means **2026-09-07 at 12:44:18 UTC**, not just September 7.
`HHMMSS` is a 24-hour clock: hours, minutes, seconds. No colons are used so names
work across operating systems. The suffix prevents same-second collisions.
`run.json` also records full ISO-format UTC timestamps, including the timezone
offset (`+00:00`). Use these rather than a copied file's modification date.

Manually requested comparison folders use
`YYYY-MM-DD_HHMMSS_<name>_<6-character-random-id>`. Automatic repetition reports
use `<first-run-id>_repetitions`, inheriting the first attempt's date and time.

Dates inside application-generated filenames have a different role. Session
files such as `pet_20260907_124424.tif` use the application's container clock when
that processing starts, so they can be a few seconds later than the run-folder
time. `hillshade_20150701_1500.tif` encodes the **simulated weather/sun time**, not
when you ran the benchmark. The runner preserves these application filenames.

Each repetition gets a distinct folder; earlier runs are never overwritten.
Failed and interrupted attempts inventory recoverable partial artifacts under the same retention policy.
Temporary files already deleted by the application cannot be recovered. Unchanged
source inputs are referenced by path and SHA-256 rather than retained repeatedly.
Required adjacent GDAL `.aux.xml`, `.ovr`, and `.msk` files are copied and fingerprinted
when present. Keep the supplied data set available to reproduce a run.

`benchmark/runs/`, `benchmark/comparisons/`, and the lock file are ignored by Git.
The scripts, this guide, and reusable scenario definitions are normal project
deliverables, not agent-only notes. Share the chosen comparison folder together
with its referenced run folders if recipients need clickable raw evidence.

## Timing, resources and interpretation

- **Request time:** Python `time.perf_counter()` around the complete HTTP exchange,
  measured inside the disposable backend container. For PET/shadow/wind the
  request goes directly to the QGIS container; tree updates go through the backend.
  These boundaries are fixed per scenario. Session preparation, startup, copying,
  hashing, validation and report generation are excluded.
- **Internal stages:** not measured. Existing logs are saved for diagnosis, but
  do not contain a reliable complete set of stage durations. The application is
  not patched, monkey-patched, seeded or profiled by this tool.
- **CPU/memory:** Docker's stream, with the actual observation timestamps saved.
  CPU can exceed 100% when multiple cores are active. Mean CPU is a sample mean;
  memory is the highest observed sample, not a guaranteed instantaneous peak.
  Sampling includes a small margin around the request, including session setup.
  It has some overhead. Missing samples are not zero usage.
- **Files:** all generated or rewritten files found in isolated data/temporary
  directories are inventoried after stopping the request containers; files are copied according to the retention policy. Final rasters are
  checked in a separate container with GDAL metadata sidecar writing disabled.
- **Output validation:** successful requests must produce readable expected
  rasters with valid cells. Tree sessions must point the updated layer at the
  filled PET raster. Raster comparisons require matching grids/CRS and report
  mean/max absolute differences plus NoData mask differences. No resampling,
  cropping, or scientific tolerance is applied silently.
- **Randomness:** existing random foliage can produce different outputs from the
  same tree positions. This is recorded as unchanged behavior. Numerical
  differences alone are not declared regressions or scientific accuracy failures.
- **Caches:** working files and containers are reset, but host/Docker filesystem
  caches are not flushed. Do not call these cold-cache timings. Keep background
  workload comparable across runs; other normal application containers remain up.
- **Comparison:** only the same hashed machine identity is accepted. Scenario,
  inputs, runtime settings/images or measurement-version changes are flagged;
  percentage improvements are suppressed for incompatible groups. Code changes
  are recorded because measuring their effect is the purpose of the tool.

## Status and exit codes

| Status | Meaning |
| --- | --- |
| `completed` | HTTP reported success and required final artifacts passed checks. |
| `blocked` | A required file or schema prerequisite was not met; no calculation request was made. |
| `failed` | The API returned an error or did not report success. |
| `invalid` | Output verification failed, or inputs/code changed during the run. |
| `indeterminate` | The request outcome could not be established, for example a timeout. |
| `interrupted` | The operator stopped the run; partial evidence is retained. |
| `error` | The tool/environment could not start the measurement. |

Exit code 0 means all requested repetitions completed, or report generation
succeeded. Exit code 2 indicates a non-completed run or tool error. Inspect the
report rather than interpreting a quick failure as improved performance.

## Isolation and recovery

The normal `qgis`/`backend` containers are inspected but never stopped or changed.
New `hsbench-*` containers use their exact image IDs, read-only application source,
a separate Docker network with no published host ports, and per-run writable
data and `/tmp` mounts. The backend's database-initialisation entrypoint is
bypassed; the benchmark routes do not use that database. Existing calculation
code and parameters are unchanged. Original input hashes are checked again after
each attempt. The tool does not build containers or alter production deployment.

The default HTTP timeout is 1200 seconds; the existing backend also has its own
1000-second upstream timeout. Set `--timeout SECONDS` for an observation limit.
A timeout can leave server work running, so the tool stops its own containers
before collecting results and never automatically repeats an uncertain attempt.

Ctrl+C or SIGTERM initiates cleanup and preserves partial evidence. After a hard
kill, crash or Docker outage, inspect:

```sh
docker ps -a --filter label=heatstress.benchmark=true
docker network ls --filter label=heatstress.benchmark=true
```

Stop/remove only the `hsbench-*` resources belonging to the interrupted run.
Then remove `benchmark/.lock` only after confirming no benchmark is active. A
stale `running` record is unfinished, not successful. Use `--keep-work` to retain
working copies for investigation. Final artifact reports reference generated
files, not a fully standalone deployable QGIS project with every source layer.

## Tests

Run both test groups with one command:

```sh
python3 tools/benchmark/bench.py test
```

Host tests include a temporary loopback HTTP server and can also run separately:

```sh
python3 -m unittest discover -s tools/benchmark/tests -p test_benchmark.py -v
```

The `test` command runs GIS tests with tiny synthetic temporary rasters in a
disposable instance of the current QGIS image. No supplied raster is mounted or
modified by those tests.

## Documentation sources

- [Project wiki](https://github.com/jrcz-data-science-lab/heatstress-digital-shadow/wiki)
- [Python elapsed-time counter](https://docs.python.org/3/library/time.html#time.perf_counter)
- [Docker resource statistics](https://docs.docker.com/reference/cli/docker/container/stats/)
- Local code: `qgis/src/api/controllers/pet_controller.py`, `shadow_controller.py`,
  `wind_controller.py`, and `backend/src/api/controllers/data_processing_controller.py`.

Known defects and meeting questions are maintained separately in the developer's
local `docs/Known_Issuess.md`. No wiki edits are published by this tool.
