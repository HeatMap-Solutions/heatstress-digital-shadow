"""Optional disk admission check for a bounded proof-of-concept dataset."""
import json
from pathlib import Path
import shutil
import stat


def check_storage_budget(root, additional_bytes=0):
    root = Path(root)
    config = root/'storage-budget.json'
    if not config.exists():
        return
    limits = json.loads(config.read_text())
    maximum = int(limits['max_dataset_bytes'])
    reserve = int(limits['minimum_free_bytes'])
    if maximum <= 0 or reserve < 0 or additional_bytes < 0:
        raise ValueError('Invalid dataset storage budget')
    # Logical sizes deliberately count hard-linked paths twice: conservative
    # for a deployment copied to a filesystem without shared extents.
    used = 0
    for path in root.rglob('*'):
        try:
            entry = path.stat()
        except FileNotFoundError:
            # Concurrent workers can remove scratch files during this scan.
            continue
        if stat.S_ISREG(entry.st_mode):
            used += entry.st_size
    dependencies = {Path(p).resolve() for p in limits.get('dependency_paths', [])}
    used += sum(p.stat().st_size for p in dependencies if not p.is_relative_to(root.resolve()))
    if used+additional_bytes > maximum:
        raise ValueError('Dataset storage budget reached; archive unused scenarios before retrying')
    if shutil.disk_usage(root).free < reserve+additional_bytes:
        raise ValueError('Insufficient disk space to preserve the configured free-space reserve')
