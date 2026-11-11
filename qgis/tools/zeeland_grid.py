# #!/usr/bin/env python3
# """Zeeland zone grid: the 140 x 280 m cells the tile tool calculates with, as a map layer.

# Writes one polygon per cell that touches Zeeland, on exactly the same lattice as
# tools/zeeland_tiles.py, so every cell lines up with the PET tiles. Fields:
# col_index/row_index (cell number), left/bottom/right/top (bounds, RD New metres),
# tile (the tile file the cell is in) and geschaalde_u_1_2 (wind, m/s; the 7.5
# placeholder for now). Takes seconds; no PET data needed.

#     python3 /app/tools/zeeland_grid.py /data/server/zeeland-grid.gpkg
# """
# import math
# from pathlib import Path
# import sys

# from osgeo import gdal, ogr, osr

# sys.path.insert(0, str(Path(__file__).resolve().parent))
# from zeeland_tiles import CELL_H, CELL_W, ORIGIN_X, ORIGIN_Y, SOURCE, WIND_PLACEHOLDER

# TILE_M = 2800  # must match the --tile-m of the tiles run (default 2800)


# def main(target):
#     gdal.UseExceptions()
#     ogr.UseExceptions()
#     target = Path(target)
#     boundary = ogr.Open(str(SOURCE/'boundary/zeeland-rd.gpkg'))
#     layer = boundary.GetLayer()
#     left, right, bottom, top = layer.GetExtent()
#     col0, col1 = math.floor((left-ORIGIN_X)/CELL_W), math.ceil((right-ORIGIN_X)/CELL_W)
#     row0, row1 = math.floor((ORIGIN_Y-top)/CELL_H), math.ceil((ORIGIN_Y-bottom)/CELL_H)
#     per_tile_x, per_tile_y = TILE_M // CELL_W, TILE_M // CELL_H

#     # One pixel per cell: a cell is kept when the Zeeland boundary touches it (fast, no per-cell geometry test).
#     touched = gdal.GetDriverByName('MEM').Create('', col1-col0, row1-row0, 1, gdal.GDT_Byte)
#     touched.SetGeoTransform((ORIGIN_X + col0*CELL_W, CELL_W, 0, ORIGIN_Y - row0*CELL_H, 0, -CELL_H))
#     touched.SetProjection(layer.GetSpatialRef().ExportToWkt())
#     gdal.RasterizeLayer(touched, [1], layer, burn_values=[1], options=['ALL_TOUCHED=TRUE'])
#     inside = touched.GetRasterBand(1).ReadAsArray()

#     partial = target.with_name(target.stem + '.partial.gpkg')
#     partial.unlink(missing_ok=True)
#     srs = osr.SpatialReference()
#     srs.ImportFromEPSG(28992)
#     out = ogr.GetDriverByName('GPKG').CreateDataSource(str(partial))
#     cells = out.CreateLayer('cells', srs, ogr.wkbPolygon)
#     for name, kind in [('col_index', ogr.OFTInteger), ('row_index', ogr.OFTInteger),
#                        ('left', ogr.OFTReal), ('bottom', ogr.OFTReal), ('right', ogr.OFTReal),
#                        ('top', ogr.OFTReal), ('tile', ogr.OFTString), ('geschaalde_u_1_2', ogr.OFTReal)]:
#         cells.CreateField(ogr.FieldDefn(name, kind))

#     count = 0
#     out.StartTransaction()
#     for row in range(row0, row1):
#         for col in range(col0, col1):
#             l, r = ORIGIN_X + col*CELL_W, ORIGIN_X + (col+1)*CELL_W
#             t, b = ORIGIN_Y - row*CELL_H, ORIGIN_Y - (row+1)*CELL_H
#             if not inside[row-row0, col-col0]:
#                 continue  # open sea
#             box = ogr.CreateGeometryFromWkt(f'POLYGON(({l} {b},{r} {b},{r} {t},{l} {t},{l} {b}))')
#             feature = ogr.Feature(cells.GetLayerDefn())
#             feature.SetGeometry(box)
#             for name, value in [('col_index', col), ('row_index', row), ('left', l), ('bottom', b),
#                                 ('right', r), ('top', t), ('geschaalde_u_1_2', WIND_PLACEHOLDER),
#                                 ('tile', f't_{col // per_tile_x:03d}_{row // per_tile_y:03d}.tif')]:
#                 feature.SetField(name, value)
#             cells.CreateFeature(feature)
#             count += 1
#     out.CommitTransaction()
#     out = None
#     partial.replace(target)
#     print(f'{count} cells -> {target}')


# if __name__ == '__main__':
#     if len(sys.argv) != 2:
#         sys.exit(__doc__)
#     main(sys.argv[1])