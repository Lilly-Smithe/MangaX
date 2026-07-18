"""Paketlenmiş MangaX edition'larını ortak kullanıcı verisi alanına geçirir."""

from __future__ import annotations

import shutil
from pathlib import Path

from mangax.core.config import DATA_DIR, LEGACY_DATA_DIR


def migrate_shared_user_data(
    legacy_dir: str | Path = LEGACY_DATA_DIR,
    shared_dir: str | Path = DATA_DIR,
) -> dict[str, int | bool]:
    source = Path(legacy_dir).resolve()
    destination = Path(shared_dir).resolve()
    if source == destination or not source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        return {"migrated": False, "files_copied": 0}

    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in source.rglob("*"):
        if item.is_symlink() or not item.is_file():
            continue
        relative = item.relative_to(source)
        target = (destination / relative).resolve()
        if not target.is_relative_to(destination):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        shutil.copy2(item, target)
        copied += 1
    return {"migrated": copied > 0, "files_copied": copied}
