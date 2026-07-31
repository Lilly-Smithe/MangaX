"""Paketlenmiş MangaX edition'larını ortak kullanıcı verisi alanına geçirir."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from mangax.core.config import (
    DATA_DIR,
    DEFAULT_DOWNLOADS_DIR,
    LEGACY_DATA_DIR,
    LEGACY_DOWNLOADS_DIR,
)


def migrate_shared_user_data(
    legacy_dir: str | Path = LEGACY_DATA_DIR,
    shared_dir: str | Path = DATA_DIR,
) -> dict[str, int | bool]:
    source = Path(legacy_dir).resolve()
    destination = Path(shared_dir).resolve()
    source_key = hashlib.sha256(str(source).encode("utf-8", errors="replace")).hexdigest()[:8]
    marker = destination / f".legacy_data_{source_key}_v1_complete"
    if marker.is_file():
        return {"migrated": False, "files_copied": 0}
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
    temporary_marker = marker.with_suffix(".tmp")
    temporary_marker.write_text("1\n", encoding="ascii")
    os.replace(temporary_marker, marker)
    return {"migrated": copied > 0, "files_copied": copied}


def _stable_conflict_destination(destination: Path, source: Path) -> Path:
    suffix = hashlib.sha256(str(source).encode("utf-8", errors="replace")).hexdigest()[:8]
    return destination.with_name(f"{destination.name}-legacy-{suffix}")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _equivalent(source: Path, destination: Path) -> bool:
    if source.is_file() and destination.is_file():
        return source.stat().st_size == destination.stat().st_size and _file_digest(source) == _file_digest(destination)
    if not source.is_dir() or not destination.is_dir():
        return False
    source_files = {
        item.relative_to(source): item
        for item in source.rglob("*")
        if item.is_file() and not item.is_symlink()
    }
    destination_files = {
        item.relative_to(destination): item
        for item in destination.rglob("*")
        if item.is_file() and not item.is_symlink()
    }
    if source_files.keys() != destination_files.keys():
        return False
    return all(
        source_file.stat().st_size == destination_files[relative].stat().st_size
        and _file_digest(source_file) == _file_digest(destination_files[relative])
        for relative, source_file in source_files.items()
    )


def _stored_path(path: Path) -> str:
    """Store absolute paths so Reader/Full installation roots can differ safely."""
    return str(path.resolve())


def migrate_legacy_downloads(
    legacy_dir: str | Path = LEGACY_DOWNLOADS_DIR,
    shared_dir: str | Path = DEFAULT_DOWNLOADS_DIR,
) -> dict[str, int | bool]:
    """Copy legacy install-local downloads and repair their SQLite paths.

    Existing destination folders are never overwritten. A deterministic conflict
    folder is used instead, which makes retries idempotent and preserves both sets.
    """
    source = Path(legacy_dir).resolve()
    destination = Path(shared_dir).resolve()
    source_key = hashlib.sha256(str(source).encode("utf-8", errors="replace")).hexdigest()[:8]
    marker = destination / f".legacy_downloads_{source_key}_v1_complete"
    if marker.is_file():
        return {"migrated": False, "files_copied": 0, "paths_updated": 0}
    if source == destination or not source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        return {"migrated": False, "files_copied": 0, "paths_updated": 0}

    destination.mkdir(parents=True, exist_ok=True)
    mappings: list[tuple[Path, Path]] = []
    copied = 0
    unresolved = False
    for item in source.iterdir():
        if item.is_symlink():
            continue
        target = (destination / item.name).resolve()
        if not target.is_relative_to(destination):
            continue
        if target.exists() and not _equivalent(item, target):
            target = _stable_conflict_destination(target, item)
        if target.exists() and not _equivalent(item, target):
            # A previous conflict copy exists but no longer matches. Preserve all
            # trees rather than picking one silently; this rare case stays on the
            # legacy path for manual recovery.
            unresolved = True
            continue
        if item.is_dir():
            if not target.exists():
                shutil.copytree(item, target, symlinks=False)
                copied += sum(1 for child in target.rglob("*") if child.is_file())
        elif item.is_file():
            if not target.exists():
                shutil.copy2(item, target)
                copied += 1
        else:
            continue
        mappings.append((item.resolve(), target.resolve()))

    paths_updated = 0
    if mappings:
        # Database import is deliberately lazy: the data directory migration must
        # finish before SQLite initializes its shared database.
        from mangax.core.database import db

        with db.get_connection() as conn:
            for table, column in (("mangas", "cover_path"), ("downloaded_chapters", "path")):
                rows = conn.execute(
                    f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"
                ).fetchall()
                for row in rows:
                    raw = str(row[column] or "")
                    candidate = Path(raw) if os.path.isabs(raw) else Path(source.parent) / raw
                    try:
                        resolved = candidate.resolve()
                    except OSError:
                        continue
                    replacement = None
                    for old_root, new_root in mappings:
                        try:
                            relative = resolved.relative_to(old_root)
                        except ValueError:
                            continue
                        replacement = new_root / relative
                        break
                    if replacement is None:
                        continue
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                        (_stored_path(replacement), row["rowid"]),
                    )
                    paths_updated += 1
            conn.commit()

    if not unresolved:
        temporary_marker = marker.with_suffix(".tmp")
        temporary_marker.write_text("1\n", encoding="ascii")
        os.replace(temporary_marker, marker)

    return {
        "migrated": copied > 0 or paths_updated > 0,
        "files_copied": copied,
        "paths_updated": paths_updated,
    }
