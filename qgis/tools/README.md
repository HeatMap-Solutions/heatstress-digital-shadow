# Generate the PET map for another area (Vlissingen)

The PET pipeline always reads the same files under `/data`. To map Vlissingen we
build those files for Vlissingen in a **separate folder** (`data-vlissingen/`)
and start the app with that folder mounted as `/data`. The Middelburg `data/`
folder stays untouched, so switching back is one command.

| File the pipeline reads | Middelburg (made by hand) | Vlissingen (made by `prepare_area.py`) |
| --- | --- | --- |
| `dsm.TIF` (0.5 m) | AHN3 sheet R_65DN1 | PDOK AHN WCS `dsm_05m` (AHN4), cut to the extent |
| `raster/svf-reproject.tif` (1 m) | KNMI SVF sheet SVF_r65dn1 | KNMI SVF_NL v3 sheets 65cn2, 65cz2, 65dn1, 65dz1, bilinear to 1 m |
| `raster/br-reproject.tif` (1 m) | LGN2018, paved → 3, other → 0.4 | same rule, scripted |
| `raster/vegetation-reproject.tif` (1 m) | unknown, looks like the inverse rule | paved → 0, other → 1 (check with step 2) |
| `json/wind_reduction copy.geojson` | wind pipeline output | 140 × 280 m grid, placeholder `u_1_2` |
| `area.json` | — | name, lat/lon for the sun position |

Vlissingen extent (RD New): x 25800–32800, y 383670–388150 — 7.0 × 4.48 km,
50 × 16 wind cells, snapped to the Middelburg wind-grid lattice.

## Steps

All commands run from the repository root. They use the `qgis` container,
which already has GDAL and numpy, so nothing needs installing on your laptop.

**1. Download LGN2018** from <https://lgn.nl/bestanden> and put the raster (or
the zip) in `sources/`, e.g. `sources/LGN2018.tif`.

**2. Check the Bowen/vegetation rule against Middelburg** (uses your existing `data/`):

```sh
docker compose build qgis
docker compose run --rm --no-deps -v ./sources:/sources qgis \
  python3 /app/tools/prepare_area.py verify-middelburg \
  --lgn /sources/LGN2018.tif --reference /data
```

Both lines should say ~100 % match. If vegetation doesn't, the hand-made file
was made differently (e.g. from NDVI) — ask Valeria before continuing.

**Free KNMI key (needed for the SVF download).** The shared anonymous key is
often used up (error 429 "Too Many Requests"). Register at
<https://developer.dataplatform.knmi.nl/register/>, log in, open the API Catalog,
click "Request an API key" for the Open Data API, and add
`-e KNMI_API_KEY=<your key>` after `--no-deps` in the build command.

**3. Build the Vlissingen data folder** (downloads about 2.5 GB, mostly SVF sheets; takes a while):

```sh
docker compose run --rm --no-deps -v ./data-vlissingen:/out -v ./sources:/sources qgis \
  python3 /app/tools/prepare_area.py build --area vlissingen \
  --out /out --lgn /sources/LGN2018.tif --reference /data
```

- `--reference /data` copies the QGIS project/styles from Middelburg and uses the
  mean `u_1_2` of the Middelburg wind grid as the placeholder. Use `--u12 <value>`
  to set the team's agreed placeholder instead.
- Rerunning is safe: downloads are cached in `data-vlissingen/_cache/`. Use
  `--only svf,lgn` to redo single steps.
- Check `data-vlissingen/prepare-report.json`: `alignment_problems` must be empty.

**4. Start the app on the Vlissingen data and generate the map:**

```sh
docker compose down
DATA_DIR=./data-vlissingen docker compose up -d     # PowerShell: $env:DATA_DIR="./data-vlissingen"; docker compose up -d
curl http://localhost:9000/pet/full-map-generation
```

The result is `data-vlissingen/pet/pet.tif`, which the QGIS project serves as
the `pet-version-1` layer. Back to Middelburg: `docker compose down` and
`docker compose up -d` without `DATA_DIR`.

**5. Look at it** in the frontend (zoom to Vlissingen) or open
`data-vlissingen/pet/pet.tif` in QGIS Desktop.

## Known differences from the Middelburg map

- **AHN version.** The WCS serves the current AHN (AHN4); Middelburg used AHN3.
  The KNMI SVF is based on AHN2, as before.
- **Wind is a constant placeholder**, so wind-driven variation in PET is absent.
- **Sea and Westerschelde** have NoData in the DSM, like Middelburg's canals.
- **Frontend extras still point at Middelburg**: the static trees BBOX in
  `frontend/src/map/utils/constants.ts` and the wind UI are Middelburg-only.
- Weather values and the reference dates (2017 vs 2015) are unchanged.
