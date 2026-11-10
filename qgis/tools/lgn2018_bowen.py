"""Reusable LGN2018 classification for Bowen ratio and the UHI vegetation proxy."""

from __future__ import annotations

import math

HIGH_BOWEN_CLASSES = frozenset({18, 19, 24, 25, 26, 27})


def classify_lgn2018(
    code: int | float | None, *, nodata: int | float | None = None
) -> tuple[float, int] | None:
    """Return (Bowen ratio, vegetation proxy), or None for a missing pixel.

    Pass the raster band's NoData value if it declares one. Pass None for a
    masked pixel. This mapping is specific to the LGN2018 class legend.
    """
    if code is None or code == nodata or not math.isfinite(code) or code <= 0:
        return None
    if code != int(code):
        raise ValueError(f"Expected an integer LGN2018 class code, got {code}")
    if code in HIGH_BOWEN_CLASSES:
        return 3.0, 0
    return 0.4, 1
