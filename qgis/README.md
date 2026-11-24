# QGIS service: PET heat-stress map for Zeeland

This service calculates the PET (Physiological Equivalent Temperature) map for
the whole province of Zeeland and recalculates it locally when a user places
trees in the app (the "3x3 update"). The model is the one from the
*Standaard Stresstest Hitte*, as in the original Middelburg pipeline.

There is one pipeline. Everything that depends on the area (data paths, the sun
position) is in `src/configs/area.py`; the code itself is not specific to Zeeland.

---

## Structure

```
qgis/
├── src/
│   ├── configs/area.py            all data paths + the fixed moment for the shadows
│   ├── services/
│   │   ├── pet_formulas.py        the PET model, once (used by the map AND the 3x3 update)
│   │   ├── pet_map_service.py     the map: tile by tile, for the whole area
│   │   ├── local_pet_service.py   the 3x3 update for one session
│   │   ├── scenario_grid.py       the wind grid: which cells are around a tree
│   │   └── raster_service.py      burning trees into the DSM and Bowen ratio
│   ├── utils/pet_tiles.py         VRT and overviews for tile files
│   └── api/controllers/pet_controller.py   /pet/status and /pet/update
├── tools/                         command-line scripts (run by a person)
│   ├── generate_pet_map.py        calculate a run
│   ├── publish_pet_map.py         show a run in the app
│   ├── build_wind_grid.py         wind GeoPackage -> grid for the 3x3 update
│   └── compare_pet.py             compare two PET maps pixel by pixel
└── tests/
```

`src/services/` holds the logic, `tools/` only reads the command line and calls it.

---

## How the map is calculated

Per zone cell (140 x 280 m), from the mean sky view factor (SVF), the mean
vegetation and the wind:

1. **Air temperature** `t_a = 28.3 + UHI`, with `UHI = (2 - svf_mean - veg_mean) x base x factor`
2. **Wet-bulb temperature** `t_w` (Stull 2011, relative humidity 44 %)
3. **Partial PET** in the sun and in the shade, from `t_a`, `t_w` and `ln(wind)`

Per pixel (1 m):

4. **Sun:** `partial + 0.546 x Bowen ratio + 1.94 x SVF`
5. **Shade:** `partial + 0.015 x SVF x Q_diff + 0.006 x (1 - SVF) x sigma x (t_a + 273.15)^4`
6. **Hillshade** of the 0.5 m DSM at 1 July 2015, 15:00: brighter than 127 -> sun PET, otherwise shade PET

Zeeland is split into tiles of 2800 x 2800 m. Each tile is calculated with one
zone cell of margin on every side, so the tiles join without seams. A VRT
(`pet.vrt`) puts the tiles together as one map.

**Wind:** the wind team's GeoPackage (250 x 125 m cells). Cells without a
calculated value use 7.5 m/s.

### Known issue in the original model: PHI

`PHI = 44` is passed to `math.sin()`, which reads it as **radians**, not degrees
(sin = 0.018 instead of 0.695). This is kept, so the results match the original
Middelburg map. Because of it, more wind gives a slightly *higher* PET in the sun.
Changing it changes every PET value in the sun and is a decision for the client.
Two tests in `tests/test_pet_formulas.py` fail on purpose if PHI is changed.

---

## Data

Paths as seen inside the `qgis` container (`./data` on the host is mounted at `/data`).
All of them are defined in `src/configs/area.py`.

```
data/
├── zeeland/                                source data
│   ├── sources/lgn2018/LGN2018.zip         land use (only used as the land mask)
│   ├── acquisition-20261005/elevation-dsm/native.vrt    AHN3 surface model, 0.5 m
│   ├── sources/svf-nl-3/manifest.json      sky view factor tiles
│   ├── boundary/zeeland-rd.gpkg            province boundary
│   └── sources/wind/
│       ├── zeeland-wind.gpkg               wind model (from the wind team)
│       └── zeeland-wind-grid.geojson       made by tools/build_wind_grid.py
├── zeeland-tiles/
│   ├── inputs/
│   │   ├── bowen_zeeland_lgn2018_1m.tif        3 = paved, 0.4 = vegetation/water
│   │   └── vegetation_zeeland_lgn2018_1m.tif   0 = paved, 1 = vegetation/water
│   └── <run>/                              one folder per run, see below
└── server/                                 what QGIS Server shows
    ├── starting-map.qgz                    copied into every new session
    ├── <run>.vrt                           the published map
    ├── styles/new-default.qml              PET colour ramp
    └── sessions/<session id>/              one folder per browser session
```

The Bowen ratio and vegetation files are made in QGIS with the Raster Calculator
on LGN2018, at 1 m, following the wiki pages for the Bowen ratio and NDVI maps
(extent 13340-77880 x 357350-421470, `COMPRESS=DEFLATE`, `TILED=YES`, `BIGTIFF=YES`).

### A run folder

```
<run>/
├── tiles/              the PET tiles  -> pet.vrt  (the map)
├── tiles-svf/          SVF            -> svf.vrt          \
├── tiles-sun-partial/  partial sun PET -> sun-partial.vrt  >  read by the 3x3 update
├── tiles-shadow-pet/   shadow PET     -> shadow-pet.vrt   /
└── report.json         settings, and time and size per tile
```

---

## Running it

All commands run in the `qgis` container (`docker compose up -d` first).

**1. Wind grid** (only when the wind GeoPackage changes):
```powershell
docker exec qgis python3 /app/tools/build_wind_grid.py
```

**2. Calculate a run.** Try a small area first (a few minutes):
```powershell
docker exec qgis python3 /app/tools/generate_pet_map.py --resolution 1 --out /data/zeeland-tiles/pilot --bbox 30000,388150,35600,393750
```
All of Zeeland (432 tiles, several hours; around 6 hours on a laptop):
```powershell
docker exec qgis python3 /app/tools/generate_pet_map.py --resolution 1 --out /data/zeeland-tiles/zeeland-1m
```
A stopped run continues with the same command: finished tiles are skipped. Keep
the computer from going to sleep during a full run.

**3. Publish it in the app:**
```powershell
docker exec qgis python3 /app/tools/publish_pet_map.py /data/zeeland-tiles/zeeland-1m/pet.vrt
```
Existing sessions then stop accepting updates ("Calculation inputs changed"), on
purpose: their trees were calculated on the previous map. See *Sessions* below.

**Compare two runs** (for example after changing the code):
```powershell
docker exec qgis python3 /app/tools/compare_pet.py /data/zeeland-tiles/pilot/pet.vrt /data/zeeland-tiles/zeeland-1m/pet.vrt
```
`"different_values": 0` means identical.

---

## The 3x3 update

When a user places, moves or deletes a tree, the frontend calls `/pet/update`.
The service recalculates only the wind cells around each changed tree (the cell
itself and its 8 neighbours) with the tree burned into the DSM and the Bowen ratio.

- The shared map is **never written to**. Each recalculated region is saved as a
  small patch file in the session folder.
- The session's map (`pet_<id>.vrt`) is the shared map plus that session's
  patches, later patches on top. It is shown as the layer *PET (Updated)*.
- Pixels that are NoData in the shared map (water, outside Zeeland) stay NoData,
  so the update does not spread into the water.
- The inputs are always taken from the run that is published in the app.

### Sessions

Each browser gets a session (cookie `session_id`) with its own folder in
`data/server/sessions/`. The trees the user placed are kept in the browser's
localStorage.

**Before a demo, or after publishing a new run**, start clean:
```powershell
Remove-Item data\server\sessions\* -Recurse -Force
```
and in the browser: F12 -> Application -> clear Local Storage and Cookies for the
address where you open the app (or use a new incognito window, after closing all others).

---

## Tests

Run in the `qgis` container, one file at a time:

```powershell
docker exec -w /app qgis python3 -m pytest tests/test_pet_formulas.py -v       # < 1 s, no data needed
docker exec -w /app qgis python3 -m pytest tests/test_local_pet.py -v          # ~15 s, no data needed
docker exec -w /app qgis python3 -m pytest tests/test_pet_map_regression.py -v # ~30 s, needs /data
```

| File | Checks |
|---|---|
| `test_pet_formulas.py` | Each formula against values worked out by hand, or published (Stull 2011) |
| `test_local_pet.py` | The 3x3 update on a small synthetic data set: exactly equal to a full recalculation, nothing outside the 3x3 cells changes, the shared map is never written, no bleeding into water, failures leave the session as it was |
| `test_pet_map_regression.py` | Recalculates one real tile and compares it pixel by pixel with the reference run (`PET_REFERENCE_RUN`, default `/data/zeeland-tiles/zeeland-1m-3x3`) |

`test_wind_pipeline.py` belongs to the wind part and calls PDOK live.

---

## Known limitations

- The frontend still lists the Middelburg layers (wind, SVF, Bowen ratio, NDVI, ...).
  They are not in the Zeeland project, so they show nothing.
- The frontend does not show the error message when an update is refused.