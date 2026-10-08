"""Selection on the supplied wind grid; independent of QGIS and raster I/O."""
from dataclasses import dataclass
import json
import math


@dataclass(frozen=True)
class Bounds:
    left: float
    bottom: float
    right: float
    top: float

    def intersect(self, other):
        result = Bounds(max(self.left, other.left), max(self.bottom, other.bottom),
                        min(self.right, other.right), min(self.top, other.top))
        return result if result.left < result.right and result.bottom < result.top else None

    def expand(self, distance):
        return Bounds(self.left-distance, self.bottom-distance,
                      self.right+distance, self.top+distance)


class ScenarioGrid:
    def __init__(self, path):
        with open(path) as stream:
            data = json.load(stream)
        if not data.get('crs', {}).get('properties', {}).get('name', '').endswith('28992'):
            raise ValueError('Scenario grid must use EPSG:28992')
        self.cells = {}
        for feature in data['features']:
            p = feature['properties']
            key = (p['col_index'], p['row_index'])
            if any(type(i) is not int or i < 0 for i in key) or key in self.cells:
                raise ValueError('Grid indices must be unique nonnegative integers')
            bounds = Bounds(p['left'], p['bottom'], p['right'], p['top'])
            if not all(math.isfinite(v) for v in vars(bounds).values()):
                raise ValueError('Grid bounds must be finite')
            ring = feature['geometry']['coordinates'][0]
            if feature['geometry']['type'] != 'Polygon' or set(map(tuple, ring)) != {
                (bounds.left, bounds.top), (bounds.right, bounds.top),
                (bounds.left, bounds.bottom), (bounds.right, bounds.bottom),
            }:
                raise ValueError('Grid geometry does not match its rectangular bounds')
            self.cells[key] = bounds
        if not self.cells:
            raise ValueError('Scenario grid is empty')
        (col, row), first = next(iter(self.cells.items()))
        self.width, self.height = first.right-first.left, first.top-first.bottom
        if self.width <= 0 or self.height <= 0:
            raise ValueError('Invalid grid cell size')
        self.west = first.left-col*self.width
        self.north = first.top+row*self.height
        for (col, row), bounds in self.cells.items():
            expected = Bounds(self.west+col*self.width, self.north-(row+1)*self.height,
                              self.west+(col+1)*self.width, self.north-row*self.height)
            if bounds != expected:
                raise ValueError('Grid must have consistent sizes, origin and indices')

    def locate(self, x, y):
        # Shared vertical edges belong to the east cell; horizontal edges to south.
        key = (math.floor((x-self.west)/self.width),
               math.floor((self.north-y)/self.height))
        if key not in self.cells:
            raise ValueError('Object is outside the scenario grid')
        return key

    def neighbours(self, x, y):
        col, row = self.locate(x, y)
        return {(col+dx, row+dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                if (col+dx, row+dy) in self.cells}

    def regions(self, selected):
        """Merge consecutive cells in each row, then identical runs in adjacent rows.

        Rectangles cover exactly the selected union, never the gaps between edits.
        """
        rows = {}
        for col, row in selected:
            rows.setdefault(row, []).append(col)
        active, completed = {}, []
        previous_row = None
        for row in sorted(rows):
            runs = []
            for col in sorted(rows[row]):
                if runs and col == runs[-1][1]+1:
                    runs[-1] = (runs[-1][0], col)
                else:
                    runs.append((col, col))
            new = {}
            for run in runs:
                start, end = run
                first, last = self.cells[start, row], self.cells[end, row]
                prior = active.pop(run, None) if previous_row == row-1 else None
                new[run] = Bounds(first.left, last.bottom, last.right,
                                  prior.top if prior else first.top)
            completed.extend(active.values())
            active, previous_row = new, row
        return completed + list(active.values())
