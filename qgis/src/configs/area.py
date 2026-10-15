"""
Location of the area the data folder describes.

Each data folder can carry an `area.json` (written by tools/prepare_area.py):

    {"name": "Vlissingen", "lat": 51.4425, "lon": 3.5735, ...}

Without that file the app behaves as before (Middelburg), so the existing
./data folder keeps working unchanged.
"""

import json
import os
from dataclasses import dataclass

DATA_PATH = os.environ.get("DATA_PATH", "/data")


@dataclass(frozen=True)
class Area:
    name: str = "Middelburg"
    lat: float = 51.498
    lon: float = 3.613


def load_area() -> Area:
    path = os.path.join(DATA_PATH, "area.json")
    if not os.path.exists(path):
        return Area()
    with open(path) as f:
        cfg = json.load(f)
    default = Area()
    return Area(
        name=cfg.get("name", default.name),
        lat=float(cfg.get("lat", default.lat)),
        lon=float(cfg.get("lon", default.lon)),
    )
