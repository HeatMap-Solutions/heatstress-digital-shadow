# Original-main PET reference snapshots

`baseline-False.npz` and `baseline-True.npz` contain the original full-raster
scenario outputs for the inputs constructed in `test_pet_baseline.py`, cropped
to their common 120 by 240 pixel extent. They are reference results, not outputs
from LocalPETService. Their interior is compared exactly, including NaNs.

Provenance: JRCZ main commit `7393948`, using its RasterService, PETService and
ShadowService, QGIS 4.3.0-Master and GDAL 3.10.3, generated 13 October 2026.
The application GDAL settings include `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`,
`GDAL_NUM_THREADS=ALL_CPUS`, `GDAL_CACHEMAX=512` and `CPL_NUM_CPUS=ALL_CPUS`.
Only the unused GDAL provider import was removed for runtime compatibility;
the hard-coded shadow alignment output folder was redirected to a private
workspace. No equations or processing parameters were changed.

For a reproducible tree realization, Python's original global random generator
was seeded with the SHA-256 integer of `tree-a`, matching the local path's
per-object seed. Baseline production normally chooses a fresh random cloud.
The snapshots cover finite -9999 NoData and extreme Float32 NoData with a NaN.
Original PROJWIN adds a south/east border; the local path retains exact source
alignment. Tests compare cores inside the 16 m processing halo.

This is computational compatibility coverage, not evidence of scientific
accuracy or equivalence for every real-data scenario. Preserve the original
missing-data convention, sparse canopy, absolute tree height, prepared wind/SVF,
15:00 hillshade and threshold-based sun/shade selection.

`baseline-active-False.npz` is a second original-main finite-sentinel output
from the long-running application container with the same image and settings.
Its 16 missing pixels were filled, while the fresh-container reference retained
them as NoData. The cause of this original-runtime variation remains unresolved.
The test accepts either complete independently generated reference core; it
does not allow per-pixel mixing or arbitrary missing-data changes. Extreme
sentinel/NaN behavior has one reference. Separate crop tests require unchanged
source values and NoData metadata.
