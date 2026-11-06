# Full Zeeland at 2 m PET / 1 m elevation - verified local trial

Completed local engineering review: 2026-10-21T21:09:50.099129+00:00. Preview: http://127.0.0.1:15176/. Codex browser opening is queued for this chat. Scientific validation and production readiness are not claimed.

## Result and storage

PET/SVF/thematic grid 32,270 x 32,060 at 2 m; DSM/DTM 64,540 x 64,120 at 1 m, EPSG 28992. Bounds [13340,357350,77880,421470]. Elevation is a virtual averaged view of immutable source archives; categories use nearest resampling. Wind and hillshade were regenerated. Fixed weather/formulas and the agreed 7.5 m/s unavailable-wind fallback remain unchanged.

The completed dataset reports 12.29 GB allocated through du, including scenario evidence. The drive has 68.19 GB free. Initial whole-run admission 80 GB included 20 GB reserve. One raw Float32 PET band is 4.138 GB versus 16.553 GB at 1 m, a 75% reduction. Compressed baseline is 1,242,239,954 bytes. Six consumed temporary maps were removed after successful checkpoints, reclaiming 3.52 GB. Required scenario inputs and all 356 manifest source entries remain.

Old Walcheren/Goes/5 m/weather trials and four archived trial-data directories were removed with compact evidence retained. The superseded 1 m dataset remains because automatic review rejected entire-directory deletion. Personal Desktop folders, required source archives and code/Git recovery material were untouched. No further cleanup is needed to fit this completed profile.

## Engineering verification

The complete 1,034,576,200 pixel audit found 436,642,995 supported pixels and zero valid PET pixels outside the polygon, on classified water, or inconsistent with support. Supported area 1746.57198 km². Source gaps exclude 5,602,097 land pixels (22.408388 km²); 394,953,465 supported pixels use fallback wind. These are coverage facts, not scientific accuracy results.

API add/repeat/delete at Middelburg passed full-raster comparisons and all 8 WMS images/legends were nonempty. Warm API add 15.77 s, repeat 0.079 s, delete 11.36 s. Chrome reviewed full province and streets in Middelburg, Goes, Zierikzee, Terneuzen and Hulst, including water edges. Five native browser tree placements changed 71 pixels only inside 45 selected cells, without support changes; repeating made no change. Removing all 5 through the canvas and saving took 201.54 s wall (200.85 s backend). Every pixel then matched baseline exactly. These two additional browser raster versions passed a fresh 12 GBpeak+20 GBreserve admission. No unlimited versions were created; extra empty browser sessions contain only 29 KB projects.

All 8 actual frontend legend endpoints return 200 with their published styles. Layer selection, PET opacity50%/100%, province/street presentation, diagnostic legends and updated PET were reviewed. Raw morphology wind has much smaller coverage than fallback PET-input wind, as its diagnostic map shows. Some first tile loads take time; screenshots include both loading frames and confirmed settled frames.

A reproduced stale readout defect was fixed: source/layer/field changes clear the previous measurement and invalidate late replies. Native Hulst wind 7.50 m/s correctly clears when DSM is selected.29 frontend tests across 5 suites, lint and production build pass; existing catalogue-age and bundle-size warnings remain. Final 56-file calculation-source hash check passes, so the frontend fix did not change formulas or rasters.

Browser automation temporarily attached a canvas ARIA label for native pointer input and detached the continuously changing FPS widget to avoid stale references. Camera destinations were set through the existing viewer; tree placement/removal used native browser pointer events. Normal reload removed those temporary DOM changes. One lost browser tab was recovered into the exact existing session after a read-only cookie check; server commit and client test state were preserved. A full reload was required after the hook-layout change during development HMR.

## Measured time

- New 2 m preparation to generated map: **4 h 52 m 40 s**.
- New 2 m preparation through automated checks, Chrome review, repairs and documentation: **7 h 2 m 39 s**.
- Whole full-Zeeland effort since 14 October 19:38:04.849 UTC, including all prior native attempts, storage pauses, cleanup, profile change and QA: **7 d 1 h 31 m 45 s**.
- Wall times include pauses and concurrent local work. These are not controlled cold benchmarks.

|Completed stage|Recorded seconds|
|---|---:|
|thematic|113.11464426002931|
|wind|5366.642539359047|
|zonal_pet|5840.2585078319535|
|svf_fill|233.07847527298145|
|svf_tiles|95.6293087520171|
|sun|100.92302500398364|
|shadow|191.3038042949047|
|hillshade|1331.5068042309722|
|combine|183.47364375006873|
|support_mask|Not separately timed|

Wind 5366.64 s is the successful resumed-stage stopwatch; the earlier 1364.35 s failed generation attempt and 220.78 s repair pause are separate. Support masking/publication is checkpointed complete but has no separate stopwatch; do not infer a precise duration. Display preparation 1734.77 s; successful automated verifier 123.89 s. Preparation stage times are in prepared/manifest.json and the complete timing JSON.

Earlier native preparation 2785.68 s; initial generation/runtime failure is retained in the old JSON. Unsuccessful 256 MiB hillshade 298861.66 s (83 h), then successful 768 MiB hillshade 3868.43 s. Storage pause before native attempt 2 was 90591.14 s; that attempt lasted 738.51 s, completed alignment and stopped before combined PET admission. All subsequent gaps remain included in total wall time.

Revised generation stopped once on a disappearing concurrent SQLite journal during storage scanning, with adequate free space. The storage-only fix was reproduced and validated with 20 tests; dependency/dataset/reserve checks remain enforced. First live verifier failed before session creation because a package import required QGIS in a GDAL-only image. Directly loading the unchanged QGIS-independent grid module repaired it. Old logs, source manifests and attempt evidence remain intact. Exact first-verifier duration and five-tree browser-add response duration were not independently retained; no fabricated timings are supplied.

## How to test

1. Open the preview and close side panels to see the entire province. Zoom to a street or search for a town/address.
2. Open Overlay Layers and compare PET, DSM, SVF, wind and other inputs. Toggle a layer and adjust opacity. Water/source gaps stay uncoloured in PET; diagnostic input coverage differs.
3. Open Heat Stress Measures, enable Objects View, select Trees, click supported land and Save. Select PET(Updated). Click the placed tree again and Save to remove it. Start with one nearby tree; first updates across distant towns can take minutes. Identical saved objects are a no-op.

Saved scenarios retain raster revisions, so use bounded tests. Existing progress labels are time estimates; /pet/status is absent, so they are not measured stage telemetry. Fixed weather, legacy dates, provisional wind fallback and source gaps are explicit limitations.

## Reproduction and custody

Dataset: primary data/zeeland/zeeland-poc-2m. Compose: compose.zeeland-2m.yml. Prepared manifest, generation.json, capacity-plan.json, code-snapshot.json, generation-runtime-state.json and run-timing.json preserve inputs, code custody, stage state and timing. Evidence: .agent-notes/evidence/zeeland-2m-verification.json, browser-verification.json, timing-final.json and completion-summary.json (all prefixed zeeland-2m-); browser-layer-review/ and cleanup-20261021/.

Preparation3GiB/3 CPU; generation4GiB/4 CPU with 2 wind workers and 768 MiB child GDAL cache. QGIS runs AMD 64 emulation on Apple M 2 / 16 GiB. Display/verifier use native ARM GDAL 3.10.3,2 GiB/3 CPU. Source mounts are read-only. KEEP_INTERMEDIATE_MAPS=0 opts into the six checkpoint-gated removals. No station retrieval is used.

Old launch/resume scripts are historical evidence, not restart commands: rerunning would collide with containers or truncate logs. New experiments need a new root/log/container set and fresh capacity admission. Do not restart completed generation for display/browser changes.

Branch feature/walcheren-pilot, HEAD7393948e2b9709726e983ebd0a37b3fd1fd6b824. Changes are uncommitted; unrelated dirty work remains intact. No commits, pushes, PRs, wiki/public publication or subagents.

The temporary15minute completion follow-up was deleted after all checks and timing documentation finished. Preview services remain running for local testing.
