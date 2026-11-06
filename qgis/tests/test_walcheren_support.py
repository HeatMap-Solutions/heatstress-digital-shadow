"""A regional update must keep water excluded and reject unsupported objects."""
import shutil

import numpy as np
import pytest

from src.services.local_pet_service import LocalPETService
from src.services.scenario_grid import Bounds
from test_local_pet import raster, read, point, prepared, session


def test_patch_keeps_excluded_water_nodata(tmp_path):
    output, patch, support = [tmp_path/name for name in ('output.tif','patch.tif','support.tif')]
    raster(output, np.full((2,3),30,dtype='float32'))
    raster(patch, np.full((2,3),20,dtype='float32'))
    mask = np.array([[1,0,1],[0,0,1]],dtype='float32')
    raster(support, mask, nodata=0)
    LocalPETService._write_patch(output,patch,Bounds(30000,393748,30003,393750),support)
    np.testing.assert_array_equal(read(output),np.where(mask == 1,20,-9999))


def test_save_rejects_object_on_water_before_publication(session, tmp_path):
    original, sid, folder = session
    root = tmp_path/'masked-inputs'
    shutil.copytree(original.root,root)
    mask = np.ones((560,280),dtype='float32')
    mask[280,140] = 0
    raster(root/'raster/pet-support.tif',mask,nodata=0)
    service = LocalPETService(root,original.sessions)
    before = (folder/'map.qgz').read_bytes()
    with pytest.raises(ValueError,match='outside supported land'):
        service.update([point()],sid)
    assert (folder/'map.qgz').read_bytes() == before
    assert not list(folder.glob('pet_*_filled.tif'))
