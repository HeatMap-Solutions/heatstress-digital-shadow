# QGIS regression tests

Run the shadow regression inside the existing QGIS service:

```bash
docker compose exec qgis python3 -m pytest tests/test_shadow_pipeline.py -q
```

The test generates four real GDAL hillshade rasters from a small flat surface.
It checks their dimensions, RD coordinate system, grid alignment, uniform
interior illumination, and response to the changing solar position. It needs
the installed QGIS/GDAL runtime but does not download data or use project rasters.

`ShadowService` calls the registered `gdal:hillshade` algorithm through
`processing.run`. Importing QGIS's internal GDAL provider module is unnecessary
and breaks when upstream relocates that module.

The separate wind integration tests require the inputs and live services
documented in `conftest.py`. Passing the shadow test does not establish the
scientific accuracy of PET or the availability of those external services.

Baseline compatibility and local-update coverage:

```bash
docker compose exec qgis python3 -m pytest tests/test_pet_baseline.py tests/test_local_pet.py tests/test_shadow_pipeline.py -q
```

The baseline snapshots come from original main `7393948`; see
`fixtures/README.md` for seed control, runtime and comparison boundaries.
