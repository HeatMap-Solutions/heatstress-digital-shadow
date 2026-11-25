#!/usr/bin/env python3
"""Compare our PET map with the province's Hittestresskaart at random points.

    python3 /app/tools/compare_hittestresskaart.py                         # 200 points, seed 42
    python3 /app/tools/compare_hittestresskaart.py --points 500 --seed 7
    python3 /app/tools/compare_hittestresskaart.py --layer klimaat:Hittestress_1_juli_2015

The same seed gives the same points, so runs can be compared over time.
Results go to /data/validation/<name>/: points.csv (one row per point) and summary.json.
The calculation is in src/services/reference_comparison.py; the reference service and
default layers are in src/configs/area.py.
"""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.configs import area
from src.services.reference_comparison import compare, rows, sample_points


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--pet', type=Path, default=area.RUNS/'zeeland-1m'/'pet.vrt', help='our map (pet.vrt of a run)')
    parser.add_argument('--points', type=int, default=200, help='number of random points with a value')
    parser.add_argument('--seed', type=int, default=42, help='same seed = same points')
    parser.add_argument('--layer', action='append', help='reference layer (repeatable); default: area.REFERENCE_LAYERS')
    parser.add_argument('--out', type=Path, help='result folder; default /data/validation/<run>-seed<seed>-n<points>')
    args = parser.parse_args()

    run = args.pet.resolve().parent
    layers = args.layer or list(area.REFERENCE_LAYERS)
    out = args.out or area.DATA_ROOT/'validation'/f'{run.name}-seed{args.seed}-n{args.points}'
    out.mkdir(parents=True, exist_ok=True)

    print(f'Sampling {args.points} points from {args.pet} (seed {args.seed}) ...', flush=True)
    shadow, svf = run/'shadow-pet.vrt', run/'svf.vrt'
    points = sample_points(args.pet, args.points, args.seed,
                           shadow if shadow.exists() else None, svf if svf.exists() else None)
    print(f'Asking {len(layers)} reference layer(s) for {len(points)} points ...', flush=True)
    values, summary = compare(points, layers, area.REFERENCE_WMS)

    table = list(rows(points, values))
    with open(out/'points.csv', 'w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    report = {'pet': str(args.pet), 'points': args.points, 'seed': args.seed, 'service': area.REFERENCE_WMS,
              'difference': 'ours - reference (°C); > 0 means our map is warmer', 'layers': summary}
    (out/'summary.json').write_text(json.dumps(report, indent=2) + '\n')

    for layer, result in summary.items():
        print(f'\n{layer}  ({area.REFERENCE_LAYERS.get(layer, "")})')
        print(f'  reference has no value at {result["reference_no_value"]} of {len(points)} points')
        print(f'  {"":6} {"n":>4} {"ours":>6} {"ref":>6} {"diff":>6} {"|diff|":>6} {"rmse":>5} {"r":>6} {"<=1C":>5} {"<=2C":>5}')
        for part in ('all', 'sun', 'shade'):
            s = result[part]
            if not s['compared']:
                print(f'  {part:6} {0:>4}')
                continue
            print(f'  {part:6} {s["compared"]:>4} {s["ours_mean"]:>6} {s["reference_mean"]:>6} {s["mean_difference"]:>6} '
                  f'{s["mean_abs_difference"]:>6} {s["rmse"]:>5} {str(s["correlation"]):>6} '
                  f'{s["within_1C_pct"]:>4}% {s["within_2C_pct"]:>4}%')
    print(f'\nSaved: {out/"points.csv"} and {out/"summary.json"}')


if __name__ == '__main__':
    main()