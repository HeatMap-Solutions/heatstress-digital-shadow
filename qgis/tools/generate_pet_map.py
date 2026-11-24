#!/usr/bin/env python3
"""Calculate the PET map tile by tile (the calculation is in src/services/pet_map_service.py).

    python3 /app/tools/generate_pet_map.py --resolution 1 --out /data/zeeland-tiles/pilot --bbox 30000,388150,35600,393750
    python3 /app/tools/generate_pet_map.py --resolution 1 --out /data/zeeland-tiles/zeeland-1m   # the whole area

A stopped run continues with the same command: finished tiles are skipped.
Afterwards, show the run in the app with tools/publish_pet_map.py.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.pet_map_service import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--resolution', type=float, required=True, choices=[1, 2], help='PET pixel size in metres')
    parser.add_argument('--out', type=Path, required=True, help='run folder, e.g. /data/zeeland-tiles/zeeland-1m')
    parser.add_argument('--tile-m', type=int, default=2800, help='tile size in metres (multiple of 280)')
    parser.add_argument('--bbox', help='only tiles touching left,bottom,right,top (RD New metres)')
    parser.add_argument('--max-tiles', type=int, default=0, help='stop after this many new tiles (timing sample)')
    args = parser.parse_args()
    bbox = [float(v) for v in args.bbox.split(',')] if args.bbox else None
    try:
        summary = generate(args.out, args.resolution, args.tile_m, bbox, args.max_tiles)
    except ValueError as error:
        raise SystemExit(str(error))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()