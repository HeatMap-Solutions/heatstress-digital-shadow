# Original-resolution Zeeland trial, 14 October 2026

The developer requested OpenCode/agent-artifact cleanup followed by an attempt
at the original Middelburg resolution. The original PET raster was directly
verified as 1 m; its DSM is 0.5 m. The trial uses native source data rather than
upsampling the 5 m regional map. Existing weather, PET equations and the
provisional missing-wind policy are unchanged.

## Cleanup

Removed 16.37 GB of explicitly approved OpenCode undo snapshots and disposable
cache. Kept the chat database, credentials, settings and actual projects.
Removed 12.41 GB from `.agent-notes`: old generated benchmark/failed rasters,
checksum-verified duplicate inputs, and retired tree-colour test rasters.
Preserved source code, active worktrees, original datasets, published maps,
current scenarios, recovery bundles, source archives, reports and measurements.
The cleanup manifests record removed files. The machine had approximately
89.7 GB free after preparing/generating the trial, before adding its preview.
Allocated-file totals are conservative and can include shared filesystem extents.

## Actual generation and checks

The selected rectangle is 2.8 x 2.8 km around Goes, intersected with the recorded
Zeeland PV29 polygon. Processing keeps the existing cell context, producing a
3080 x 3360 m rectangle. Sources remain recorded AHN3, SVF_NL/3, LGN2018 and
provincial BAG/BGT. Preparation produces 6160 x 6720 DSM/DTM pixels at 0.5 m,
and 3080 x 3360 SVF/PET pixels at 1 m, in EPSG:28992.

Preparation took 53.23 s. Main recorded calculation stages total 292.84 s,
including 254.78 s for four wind windows. Generation and verification both
finished with exit 0 and no OOM under a 3 GiB Docker RAM cap and two CPUs.
An observed container peak was 2.16 GB; this sample is not a complete peak log.
The main generation process reported peak RSS 909076 KiB, excluding child
processes. Other application stacks and cleanup were active, so these are
feasibility observations rather than controlled performance benchmarks.

PET covers 7.047304 km2 of 7.392475 km2 identified land. The remaining
0.345171 km2 stays masked. An audit of all 10348800 pixels found zero valid
PET pixels outside the polygon, zero on mapped water, and zero differences
between PET validity and the support mask. Missing wind uses the provisional
fallback on 6.19% of supported pixels. Scientific validation remains pending.

The real local calculation service added a 15 m tall, 5 m radius tree in 3.68 s
and selected nine cells. Forty-six pixels changed, none outside those cells,
with unchanged validity. Repeat was a no-op; deletion took 2.57 s and restored
every pixel exactly. The separate backend API also passed add/repeat/delete (9.23/0.03/4.60 s),
and all eight WMS overlays and legends returned valid data. These checks
establish calculation consistency, not the
scientific accuracy of tree effects or province-wide completion.

Before verification scenarios, application data was 0.310 GB logical and
0.314 GB summed allocated sizes. The complete trial was 0.993 GB logical,
including 0.439 GB of unchanged provincial vector caches. DSM appears twice
as a hard link, so summed sizes overcount its physical storage. Compression
varies by terrain and NoData; this small urban trial cannot predict an exact
provincial footprint.

## Earlier full-province attempt and the 100 GB target

The actual full-province 1 m preparation attempt was rejected by the capacity
check before writing DSM. At the recorded provincial processing extent, one
uncompressed 0.5 m Float32 elevation band is 66.2128768 GB. The existing
100 GB profile allows 60 GB for all generation data, reserving space for
software and free disk. Compression may reduce the actual result, but the
current conservative admission policy cannot establish a safe full-province
run. Full Zeeland remains 5 m; whole-province 1 m was not generated.

The trial exposed a storage estimate that used 1 m PET pixels for 0.5 m
DSM/DTM. Preparation now estimates the actual elevation grid. The behavioral
regression fails with the previous formula and passes with the corrected one.
The relevant preparation/coarse suites passed 10 cases. The old formula
failed the new regression with DID NOT RAISE, demonstrating the premature
admission rather than merely checking implementation text. Existing writer
warnings and a read-only pytest-cache warning are recorded in the evidence.
No weather or PET equations changed.

## Preview and evidence

`compose.original-resolution-trial.yml` provides a separate localhost preview
at http://127.0.0.1:15175/ with backend 18002, calculator 19002 and WMS 18012.
Set `ZEELAND_1M_TRIAL_DATA_PATH` to the generated Goes trial `app-data` directory.
It preserves the existing 5 m Zeeland and 1 m Walcheren previews. The trial profile sets `VITE_REGION_VIEW` to a validated JSON array of
west, south, east, north and camera height, so initial view and Home frame
Goes automatically. Other profiles retain their existing views.

The dataset is `data/zeeland/zeeland-original-resolution-trial/goes-1m`.
Boundary/source metadata, preparation and generation records, and the source
hash snapshot are retained there. Local evidence is under
`.agent-notes/evidence/original-resolution-*`; cleanup inventories are under the
primary checkout's `.agent-notes/*cleanup*20261014.json`. Source changes remain
uncommitted. No push, PR, public deployment or wiki publication was performed.

Frontend validation passed 27 tests, lint, and both the default and configured
native-trial production builds. The retained bundle-size warning is separate
from calculation validation. Native-view configuration does not alter PET.

Fresh browser navigation verified that the trial opens over Goes with its PET
overlay visible; the screenshot is `original-resolution-frontend.png`.

## Native provincial run with a larger local storage allowance

The developer subsequently approved more local storage. The new isolated
`data/zeeland/zeeland-poc-1m` run retains 1 m PET/SVF and 0.5 m DSM/DTM,
using the official PV29 polygon and the same existing weather and equations.
Original archives remain immutable. DSM/DTM preparation writes cropped GDAL
virtual rasters instead of materializing two 66.21 GB uncompressed bands.
These descriptors reference the native archive mosaic; they do not reduce
resolution or replace the source data. The 119 retained DSM archive dependencies
occupy 16.49 GB and are included in runtime dataset accounting. Generation also
needs the retained DTM and SVF sources. VRT file size alone is not a hosting size.

Native wind still uses the existing 140 x 280 m lattice, west wind, and
2.8 x 2.8 km windows with a one-cell context. Each window runs in a fresh
Python/QGIS process. Its private TMPDIR/CPL_TMPDIR is removed after exit,
releasing native allocations and intermediate files before the next window.
Checkpoints retain their hashes. Filled SVF and hillshade use lossless tiled
DEFLATE storage. These are storage/lifecycle changes, not changes to PET physics.
The local dataset allowance is 100 GB plus unrelated retained development data;
physical disk admission still preserves 20 GB free. This is separate from the
earlier 100 GB complete-host profile.

A second complete Goes run verified the new virtual path against the earlier
materialized native trial. All 10,348,800 PET pixels, all support-mask/SVF pixels,
all 41,395,200 native hillshade pixels, and all 264 wind-cell properties match
exactly. Its main recorded stages total 688.06 s, including 512.15 s wind,
with tests and provincial preparation sharing the machine. These timings are
not a controlled performance comparison. Real scenario add selected nine cells
and changed 46 pixels; none changed outside those cells or altered validity.
Repeat was a no-op and deletion restored all PET pixels exactly. Add/delete
were 46.48/30.78 s under concurrent load. The applicable GIS suite passed
42 cases, with 26 existing writer deprecation warnings. A separate real window
verified the final fresh-process scratch cleanup. That cleanup setting was
added after the comparison parent had started, so its check is recorded
separately rather than treating the earlier manifest as a hash of the final code.

Full provincial preparation is running. Province-wide native generation,
coverage, runtime, disk use, scenario behavior and publication must be checked
before claiming the whole 1 m proof complete. The source snapshot is retained
in the dataset root; no commit, push or public deployment has been performed.

The local provincial preview configuration is `compose.original-resolution-poc.yml`.
Set `ZEELAND_1M_DATA_PATH` to this run's `app-data`, and
`ZEELAND_NATIVE_SOURCE_PATH` to retained `data/zeeland/acquisition-20261005`.
QGIS and WMS mount those source archives read-only at their recorded paths;
the descriptor alone is not portable. After generation completes, the profile
uses frontend 15176, backend 18003, QGIS 19003 and WMS 18013. The original
Goes/Walcheren/coarse previews remain separate.

## Provincial continuation and display checks

Full preparation finished successfully: DSM/DTM retain 0.5 m native virtual
inputs, SVF is 1 m (5.510 GB), and categorical land use is 1 m (36.91 MB,
nearest sampling from LGN2018). SVF preparation recorded 2174.89 s and classes
441.93 s; that wall time includes concurrent work and a short deliberate pause.
The full 1 m processing grid is 64540 x 64120, or 4,138,304,800 PET pixels.
Thematic preparation took 774.70 s. Provincial generation is now calculating
native wind in two isolated processes under a 6 GiB memory cap and six CPUs.
These are process/resource settings, not evidence of a completed provincial map.

The two-worker Goes trial matches all PET, support-mask, SVF, hillshade and
wind-cell output values against the materialized native reference. Recorded
trial stages total 310.50 s, including 204.90 s wind. This is not a controlled
speedup benchmark against the earlier loaded serial run. Six generation
regressions passed, including concurrent assembly, checked checkpoint reuse
and rejection of shared-process GIS parallelism. Twenty-one local scenario
regressions passed, including internal overview refresh after add/delete;
23 existing writer deprecation warnings remain.

Display preparation uses official GDAL 3.10.3 on native ARM, rather than
changing the QGIS calculation runtime. PET/SVF use average display overviews;
Bowen/vegetation categories use nearest. Native DSM uses a plain identity VRT
around its cropped warped VRT, allowing physical external overviews. Direct
WarpedVRT overview building only creates virtual descriptions, and GDAL can
rewrite relative source paths. Those initial attempts were rejected. The final
identity wrapper preserves all 41,395,200 Goes DSM pixels exactly, and its
seven physical overviews survive reopening. The PET native byte-stream hash
is also identical before and after display preparation. The temporary wrapper
is atomically renamed; native archives remain unchanged.

Local scenarios copy internal PET overviews with the previous raster and
refresh only changed regions before publishing. GDAL 3.10's Python binding
does not expose partial-refresh options; the installed gdaladdo CLI supports
partial refresh from the projected extent. A real add/delete regression checks
every overview pixel against a complete rebuild, with unchanged native pixels.
Runtime native-source mounts are limited to elevation-dsm, ahn3-dsm and
bulk-dsm, retaining the recorded paths without including DTM generation data
in the serving profile.

A one-off continuation waits for successful generation, builds display data,
starts the localhost profile, and audits every native pixel for polygon/water/
support consistency. It then performs real API tree add/repeat/delete, compares
every native PET pixel, and checks eight non-empty WMS overlays and legends.
It keeps the Mac awake while running and restores the six older preview
services on exit. Logs and the exact command/source snapshot remain local.
No province-wide completion or numerical/scientific validation is claimed
until those checks actually pass; browser inspection remains a final check.

The developer requested the complete duration. The dataset's `run-timing.json`
records the original preparation/generation start times and benchmark conditions;
`generation-runtime-state.json` retains Docker's actual generation finish time
and exit status when it finishes. Preparation and generation manifests retain
individual stage times. Display preparation and live verification record their
own UTC start/finish times and elapsed seconds. Report the preparation-to-ready
wall time separately from calculation stages, per-window wind times, retries,
and the known preparation pause/concurrent load. Summing parallel wind-window
durations is worker time, not the elapsed wind stage.
Successful automated verification also writes
`.agent-notes/evidence/zeeland-native-1m-timing-final.json`, combining those clocks
and the preparation-to-verification elapsed time. Browser inspection remains
separate and pending until performed.

At 01:44 on 15 October (Europe/Amsterdam), overnight readiness checks found
the original waiting shell absent from the host process list; its old execution
session was unavailable. The cause is unconfirmed. Generation itself remained
running and checkpointing, so it was not restarted. The one-off continuation
was restored using a detached session with redirected logs and SIGHUP ignored.
It retains the same display/verification commands, captures Docker's generation
finish state before testing, and restores the six older preview services on exit.
The launcher exits immediately; a subsequent host process check verified the
shell, docker wait and sleep assertions remain alive. Evidence is in
`overnight-handoff.json` and `zeeland-native-1m-overnight-handoff.log`.
A separate timed assertion prevents idle/system sleep until 10:00 on
15 October; the generation/test assertion also remains active until its job
finishes. The approved follow-up handles browser checks and a morning report.


## Native stage-admission interruption and recovery

The first provincial attempt ended at 09:16:46 on 15 October Europe/Amsterdam
before hillshade: the generic admission reserved 33.11 GB plus the 20 GB free
reserve, while 52.1 GB was free. Completed wind, zonal PET, SVF, sun and shade
outputs remain checkpointed. Native Byte hillshade needs at most 16.55 GB raw;
combination has two sequential 16.55 GB Float32 outputs. The one-off recovery
admits each output separately with 2 percent plus 16 MiB overhead, retaining the
existing 20 GB reserve and dataset maximum. No caches, source data or trial maps
were deleted, and every frozen source/checkpoint hash is checked unchanged.

The recovery calls the unchanged ShadowService and replays the same GDAL Warp
and rastercalculator parameters. Its complete native Goes comparison matched
all 10,348,800 pixels and grid/NoData metadata exactly before provincial work.
It records actual executed stages and then resumes the unchanged generator for
support masking and publication. This is an operational replay, not an
uninterrupted cold run of the original stage wrapper. Timings retain the first
Docker attempt, the recovery interval and the resumed attempt. Full-map tests
remain required before completion. Evidence is native-storage-recovery.json
in the dataset and native-storage-recovery.py / recover-native-tail.zsh /
zeeland-native-1m-recovery-stages.log under the worktree evidence directory.


### 18 October: approved hillshade cache restart

Human approval authorized restarting only unfinished hillshade with 768 MiB cache. The full-width 33,302,640-pixel cache experiment matched an independently materialized original-cache reference exactly, including grid, CRS, type and NoData; this is engineering equivalence evidence, not scientific validation or a whole-province speed estimate.

All 55 frozen calculation source hashes matched before restart. Completed thematic, wind, zonal PET, SVF, sun and shade checkpoints remain intact. The old hillshade attempt ran from 15 October 07:28:36 UTC until intentional stop on 18 October 18:29:37 UTC, approximately 83 h 1 min. Its partial TIFF (2,688,832,972 bytes), scripts, logs and state were preserved under dataset recovery-attempts/20261018T182907Z-cache256. Nothing was deleted.

The new detached recover-native-tail-cache768.zsh (driver22068, caffeinate22069) starts zeeland-native-1m-finish-stages-cache768; new stage/handoff logs include recovery-cache768 in their names. Parent GDAL and processing subprocess launch both use 768 MiB. Frozen formulas, parameters, resolution, source/checkpoint checks, 4 GiB memory cap, dataset maximum and 20 GB free reserve remain unchanged. Free disk at launch was 37.059 GB against a 36.901 GB admission requirement. Detailed exact timestamps, operational SHA and archive custody: dataset hillshade-cache768-restart.json. Display builder cache remains256.

After hillshade and combination succeed, the driver resumes the unchanged generator for support masking/publication, then the existing display and automated verification handoff. Browser verification across five Zeeland towns remains mandatory. Final wall time must include the archived attempt and restart gap.


18 October storage follow-up: The official Docker desktop-reclaim-space helper completed successfully and reclaimed only184KiB of allocated Docker.raw blocks. No images, containers, volumes, source data or trial data were removed. At this check free space was approximately37GB, close to the next per-output admission requirement36.901GB including the unchanged20GB reserve. Hillshade remains live; the user was asked for approximately20GB additional working space or an external SSD before later admission becomes blocked. Exact observations are in dataset hillshade-cache768-restart.json. Docker credential helper stalled on the public helper pull; a temporary empty Docker configuration downloaded the public image anonymously without changing the existing login configuration.


18 October 19:34 UTC: Native hillshade completed successfully and was checkpointed after3868.431594s (64m28s including220.172s setup; gdaldem command approximately3648.8s). Output4,668,033,194 bytes; SHA-256 aef1907dd80efccc915ecf57fa7f87c194545128eb4e20b1d3ade145d29d89e5. Native ARM GDAL metadata confirms129080x128240, EPSG28992, transform[13340,0.5,0,421470,0,-0.5], Byte, NoData0. Metadata and hash establish readable checkpoint custody, not scientific accuracy.

The next aligned Float32 output was refused before writing because free space was below36.901GB including the20GB reserve. Container exited1 without OOM at19:34:56.537231258Z. This is a storage admission block after successful hillshade, not a failed hillshade. Completed stages remain intact. Preserve this checkpoint and skip hillshade on any authorized continuation. The six older previews were restored by the EXIT trap. User approved archiving all four Desktop folders (Year1-3, Korea, zdj, Projects for portfolio); backup destination Windows PC or external SSD is still pending. No files moved/deleted. Snapshot record: dataset native-storage-recovery-cache768-attempt-1.json; metadata: .agent-notes/evidence/zeeland-native-1m-hillshade-gdalinfo.json.


Completed hillshade integrity follow-up: native ARM GDAL3.10.3 gdalinfo -checksum read the entire16,553,219,200-pixel Byte band successfully (exit0), checksum44424. Read-only mount and disabled PAM preserved the file; observed memory303.8MiB within1GiB cap. Full JSON: .agent-notes/evidence/zeeland-native-1m-hillshade-full-readcheck.json. This verifies whole-band readability in addition to recorded SHA/grid metadata; no scientific accuracy claim. Combination remains blocked by free-space admission and backup destination remains pending.


### Native tail resumed, 19 October 2026 20:44 UTC

Available storage satisfied the unchanged 36,901,060,800-byte output admission (37,852,667,904 bytes at launch). The existing prepared continuation was launched at 2026-10-19T20:44:47.677838Z, detached script PID 75416 and caffeinate PID 75418. It verifies the completed hillshade SHA before restarting the existing cache768 recovery, skips checkpointed hillshade, then continues the original generator and display/verification handoff. Initial process inspection confirmed PPID 1, active caffeinate and checksum guard. No source files were moved or deleted; backup destination remains pending.

The storage pause since 2026-10-18T19:34:56.537231Z was 90,591.140607 seconds and must be included in final preparation-to-verified time. Dataset evidence: `native-tail-resume-20261019T204447Z.json`. Handoff log: `.agent-notes/evidence/zeeland-native-1m-resume-20261019T204447Z.log`; stage log: `.agent-notes/evidence/zeeland-native-1m-recovery-cache768-attempt2-stages.log`. Generation, publication and automated/browser verification remain pending. This resume is operational evidence, not scientific validation.


19 October 20:59 UTC update: Space temporarily satisfied admission and the prepared continuation resumed at20:44:47.677838Z after a90,591.140607s storage pause. The hillshade SHA guard passed; all55 frozen source hashes passed and the10,348,800-pixel bounded combine reference remained exact. Recovery container ran20:45:37.303937552Z to20:57:55.811758171Z (738.507821s), exit1, no OOM. Native Warp produced the completed aligned raster shadow-maps/shadow_map_aligned.tif:1,672,731,965B, SHA256 edc82003190c03c962698c4167f7027b21d60f6d7facaf714f659fcb9cee2927. Native GDAL confirms64540x64120 Float32 NoData-9999. The second per-output admission then refused combined PET creation; free space after evidence collection35,611,594,752B versus36,901,060,800B required. No combined.tif or combine checkpoint exists. All eight completed stage checkpoints and aligned raster are preserved; hillshade was not recomputed. Driver75416/caffeinate75418 exited, six older previews restored. No files moved/deleted; archive destination still pending.

Evidence: dataset native-storage-recovery-cache768-attempt-2.json and native-tail-resume-20261019T204447Z.json; .agent-notes/evidence/zeeland-native-1m-recovery-cache768-attempt2-stages.log and zeeland-native-1m-aligned-shadow-gdalinfo.json. Preserve these logs and the completed aligned raster before any later continuation. Do not blindly reuse the attempt2 driver because it truncates its log and overwrites the aligned output. The20GB reserve remains unchanged. Final timing must include both storage pauses and this incomplete combine attempt. Whole-native-Zeeland publication, automated verification and browser review remain unfinished. Metadata/hash are engineering custody evidence, not scientific validation.
