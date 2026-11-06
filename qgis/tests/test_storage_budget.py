import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.utils.storage_budget import check_storage_budget


def budget(root, maximum=10000, reserve=100, dependencies=()):
    (root / 'storage-budget.json').write_text(json.dumps({
        'max_dataset_bytes': maximum, 'minimum_free_bytes': reserve,
        'dependency_paths': [str(p) for p in dependencies],
    }))


def test_disappearing_worker_journal_does_not_abort_admission(tmp_path, monkeypatch):
    budget(tmp_path)
    journal = tmp_path / 'worker.gpkg-journal'
    journal.write_bytes(b'scratch')
    original = Path.stat
    observed = []

    def stat_with_worker_removal(path, *args, **kwargs):
        if path == journal:
            observed.append(path)
            entry = original(path, *args, **kwargs)
            path.unlink()
            return entry
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', stat_with_worker_removal)
    check_storage_budget(tmp_path)
    assert observed


def test_storage_limits_remain_enforced(tmp_path, monkeypatch):
    budget(tmp_path, maximum=2000)
    (tmp_path / 'retained.tif').write_bytes(b'x' * 2001)
    with pytest.raises(ValueError, match='Dataset storage budget'):
        check_storage_budget(tmp_path)
    budget(tmp_path, reserve=100)
    monkeypatch.setattr('src.utils.storage_budget.shutil.disk_usage',
                        lambda root: SimpleNamespace(free=150))
    with pytest.raises(ValueError, match='free-space reserve'):
        check_storage_budget(tmp_path, additional_bytes=51)


def test_missing_required_dependency_still_fails(tmp_path):
    budget(tmp_path, dependencies=[tmp_path.parent / 'missing-source.tif'])
    with pytest.raises(FileNotFoundError):
        check_storage_budget(tmp_path)


def test_permission_failure_is_not_silenced(tmp_path, monkeypatch):
    budget(tmp_path)
    retained = tmp_path / 'retained.tif'
    retained.write_bytes(b'data')
    original = Path.stat

    def denied(path, *args, **kwargs):
        if path == retained:
            raise PermissionError('denied')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', denied)
    with pytest.raises(PermissionError):
        check_storage_budget(tmp_path)
