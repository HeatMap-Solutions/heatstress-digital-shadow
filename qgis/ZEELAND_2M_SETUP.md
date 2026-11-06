# Zeeland 2 m PET / 1 m elevation implementation

This branch preserves the implementation used for the completed local Zeeland
trial on 21 October 2026, including local tree updates and regional display
layers. It is an experimental software result, not scientific validation or a
production release. Source archives and generated map data are not in Git.

## What is included

- `tools/prepare_walcheren.py`: regional input preparation, despite its historical
  filename. `REGION_ROOT` selects the regional dataset, and `PET_RESOLUTION_M=2`
  selects 2 m PET with 1 m elevation views.
- `tools/generate_walcheren.py`: windowed wind processing, regional zonal and
  raster PET stages, support masking, checkpoints and QGIS project publication.
- `src/services/local_pet_service.py`: local scenario calculations with aligned
  crops, context margins, support checks, unchanged-state detection and atomic
  project publication. It retains the existing 3 x 3 neighborhood approach.
- `src/utils/raster_sources.py` and `storage_budget.py`: virtual-source handling
  and storage admission.
- `../compose.zeeland-2m.yml`: local application services and regional frontend
  configuration.
- Backend/frontend changes for regional projects, diagnostic legends, map
  framing, object handling and clearing stale feature-info measurements.
- Regression tests and small original-main numerical reference fixtures.

The other trial documents and Compose files describe earlier experiments. Their
resolution and ports are not substitutes for this final profile.

## Open an existing generated dataset

From the repository root, set paths for your own retained data:

```bash
export ZEELAND_2M_DATA_PATH=/absolute/path/zeeland-poc-2m/app-data
export ZEELAND_NATIVE_SOURCE_PATH=/absolute/path/acquisition-20261005
docker compose -f compose.zeeland-2m.yml up -d
```

The preview is at <http://127.0.0.1:15176/>. The Compose file references the
existing local `heatstress-digital-shadow-{qgis,backend,frontend}` images. A new
machine must build/provide these images and supply application environment
configuration separately. Environment files and credentials must remain local.

The application data needs the generated project, PET raster, scenario inputs
and styles. Virtual elevation views also require their referenced source paths
to resolve inside the containers. Copying a VRT alone does not copy its data.
Do not invoke the legacy `/pet/full-map-generation` endpoint to reproduce this
regional workflow; that endpoint still uses the original prepared input paths.

## Generation prerequisites and profile

A fresh clone is not a complete input acquisition package. Before generation,
provide the recorded regional boundary, acquisition manifests/elevation VRTs,
cached provincial building/tree vectors, SVF manifest and files, LGN2018 archive,
and the existing PET style. The preparation and generation tools enforce their
expected paths and manifests. See `WALCHEREN_PILOT.md` for input identities and
`ZEELAND_2M_TRIAL.md` for the final profile and retained evidence inventory.

The successful local configuration used:

```text
REGION_ROOT=/data/zeeland/zeeland-poc-2m
PET_RESOLUTION_M=2
ELEVATION_STORAGE=virtual              # preparation
FRESH_WIND_PROCESS=1                  # generation
WIND_WORKERS=2                        # generation
GDAL_CACHEMAX=768                     # child-process environment, MiB
KEEP_INTERMEDIATE_MAPS=0               # checkpoint-gated temporary removal
```

Preparation ran with 3 GiB / 3 CPUs; generation with 4 GiB / 4 CPUs. The GDAL
environment setting is not an aggregate memory limit: some parent operations
set their own cache explicitly. The initial capacity admission required 80 GB
free, comprising 60 GB additional working allowance and a 20 GB reserve. These
are planning allowances, not measured peak storage.

Mount source data read-only, mount only the chosen run directory writable, and
run `python3 /app/tools/prepare_walcheren.py` followed by
`python3 /app/tools/generate_walcheren.py` in the configured QGIS image. The latter
publishes the initial QGIS project and baseline raster. The final local review
also prepared display overviews before starting the preview; its one-off
display/verification scripts remain in local evidence and are not bundled here.
Use a separate run directory and distinct container/log names for new runs.
Do not blindly restart historical handoff scripts or overwrite a completed map.

## Recorded result and limits

- PET grid: 32,270 x 32,060 at 2 m, EPSG:28992. Elevation: 1 m virtual averaged
  views of retained sources. The raw Float32 PET band is 4.138 GB.
- Preparation through generation: 4 h 52 m 40 s. Through automated/browser
  checks, repairs and documentation: 7 h 2 m 39 s. These are observed wall times,
  not a controlled before/after performance benchmark.
- Final dataset allocation: 12.29 GB including scenario evidence; compressed
  baseline PET: 1.24 GB. Source archives and Docker assets are additional.
- Full-grid consistency checks covered 1,034,576,200 pixels. Five-town browser
  tree tests changed only selected areas and deletion restored the baseline
  exactly. Detailed evidence was retained locally, not uploaded as large data.
- Warm single-tree API add/repeat/delete took 15.77 / 0.079 / 11.36 seconds.
  Cold or distant edits can take minutes. Changed scenarios still copy a full
  raster revision, so saved revisions consume disk space.
- Approximately 90.45% of supported PET pixels use the provisional 7.5 m/s
  pedestrian-wind fallback. Complete provincial morphology wind remains
  unfinished. Tree edits reuse wind-dependent inputs; they do not recalculate
  regional wind reduction.
- The Wind Reduction debug panel's legacy default DSM/DTM paths were absent in
  the recorded deployment. Its existence is not evidence of working generation.
- Fixed weather, mixed legacy dates and source gaps remain. The final support
  mask combines the official Zeeland boundary, LGN water classes 16/17 and
  required-input support. A dedicated quantitative tile-seam audit remains open.

## Regression checks

With the preview services available:

```bash
docker compose -f compose.zeeland-2m.yml exec frontend npm test -- --runInBand
docker compose -f compose.zeeland-2m.yml exec frontend npm run lint
docker compose -f compose.zeeland-2m.yml exec frontend npm run build
docker compose -f compose.zeeland-2m.yml exec backend python3 -m unittest discover -s tests -v
docker compose -f compose.zeeland-2m.yml exec qgis python3 -m pytest tests/test_pet_baseline.py tests/test_local_pet.py tests/test_shadow_pipeline.py tests/test_storage_budget.py tests/test_virtual_rasters.py tests/test_coarse_region.py tests/test_walcheren_generation.py tests/test_walcheren_preparation.py tests/test_walcheren_support.py -q -p no:cacheprovider
```

These small regression checks do not regenerate the province. Wind integration
tests have additional live-service/source-data requirements; see `tests/README.md`
and `tests/conftest.py`. Numerical fixture agreement establishes compatibility
in those test domains, not independent scientific accuracy.
