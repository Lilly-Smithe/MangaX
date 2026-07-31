"""ZIP/CBZ, görsel ve klasörleri MangaX'in yönetilen yerel alanına aktarır."""

from __future__ import annotations

import os
import re
import shutil
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from PIL import Image, ImageOps

from mangax.core.config import LOCAL_MANGA_DIR
from mangax.core.dependencies import library_manager
from mangax.core.image_safety import validate_image_dimensions


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ARCHIVE_EXTENSIONS = {".zip", ".cbz"}
MAX_PAGE_COUNT = 10_000
MAX_PAGE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
THUMBNAIL_SIZE = (360, 540)
ProgressCallback = Callable[[int, int, str], None]


class ImportCancelled(Exception):
    pass


def _check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event and cancel_event.is_set():
        raise ImportCancelled("Manga ekleme iptal edildi.")


def _natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w\-. ]+", "", str(value), flags=re.UNICODE).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:100] or fallback).strip()


def _image_files(directory: Path, *, recursive: bool = False) -> list[Path]:
    iterator: Iterable[Path] = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        (path for path in iterator if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS),
        key=lambda path: _natural_key(path.as_posix()),
    )


def _folder_chapters(source: Path) -> list[tuple[str, list[Path]]]:
    chapters: list[tuple[str, list[Path]]] = []
    root_pages = _image_files(source)
    if root_pages:
        chapters.append(("Bölüm 1", root_pages))
    for child in sorted((item for item in source.iterdir() if item.is_dir()), key=lambda p: _natural_key(p.name)):
        pages = _image_files(child, recursive=True)
        if pages:
            chapters.append((_safe_name(child.name, f"Bölüm {len(chapters) + 1}"), pages))
    return chapters


def _validated_archive_images(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    images: list[zipfile.ZipInfo] = []
    total = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or path.suffix.casefold() not in IMAGE_EXTENSIONS:
            continue
        if info.flag_bits & 0x1:
            raise ValueError("Şifreli ZIP/CBZ dosyaları desteklenmiyor.")
        if info.file_size > MAX_PAGE_BYTES:
            raise ValueError("Arşivde izin verilenden büyük bir sayfa var.")
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Arşiv açılmış boyut sınırını aşıyor.")
        images.append(info)
        if len(images) > MAX_PAGE_COUNT:
            raise ValueError("Arşivde izin verilenden fazla sayfa var.")
    return sorted(images, key=lambda item: _natural_key(item.filename))


def _archive_groups(images: list[zipfile.ZipInfo]) -> list[tuple[str, list[zipfile.ZipInfo]]]:
    paths = [PurePosixPath(item.filename.replace("\\", "/")) for item in images]
    while paths and all(len(path.parts) > 1 for path in paths) and len({path.parts[0] for path in paths}) == 1:
        paths = [PurePosixPath(*path.parts[1:]) for path in paths]
    grouped: dict[str, list[zipfile.ZipInfo]] = {}
    for info, path in zip(images, paths):
        key = path.parts[0] if len(path.parts) > 1 else ""
        grouped.setdefault(key, []).append(info)
    if len(grouped) == 1:
        return [("Bölüm 1", images)]
    result = []
    for key in sorted(grouped, key=_natural_key):
        title = _safe_name(key, f"Bölüm {len(result) + 1}")
        result.append((title, grouped[key]))
    return result


def _copy_folder_chapter(
    pages: list[Path],
    destination: Path,
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    progress_state: dict | None = None,
) -> list[str]:
    destination.mkdir(parents=True)
    names = []
    total = 0
    if len(pages) > MAX_PAGE_COUNT:
        raise ValueError("Klasörde izin verilenden fazla sayfa var.")
    for index, source in enumerate(pages, start=1):
        _check_cancelled(cancel_event)
        size = source.stat().st_size
        if size > MAX_PAGE_BYTES:
            raise ValueError("Klasörde izin verilenden büyük bir sayfa var.")
        if progress_state is not None:
            progress_state["bytes"] = progress_state.get("bytes", 0) + size
            total_bytes = progress_state["bytes"]
        else:
            total += size
            total_bytes = total
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("Manga boyut sınırını aşıyor.")
        name = f"{index:05d}{source.suffix.casefold()}"
        with source.open("rb") as input_file, (destination / name).open("wb") as output_file:
            while chunk := input_file.read(1024 * 1024):
                _check_cancelled(cancel_event)
                output_file.write(chunk)
        names.append(name)
        if progress_state is not None:
            progress_state["current"] += 1
            if progress_callback:
                progress_callback(progress_state["current"], progress_state["total"], "Sayfalar kopyalanıyor…")
    return names


def _create_cover_thumbnail(source: Path, destination: Path) -> None:
    """Kartlarda tam manga sayfasını decode etmemek için küçük bir kapak üret."""
    with Image.open(source) as image:
        validate_image_dimensions(image)
        image = ImageOps.exif_transpose(image)
        image.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGB")
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image = background
        image.save(destination, "WEBP", quality=78, method=4)


def import_local_manga(
    selected_path: str | os.PathLike[str],
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    source = Path(selected_path).expanduser().resolve()
    if not source.exists():
        raise ValueError("Seçilen dosya veya klasör bulunamadı.")
    if source.is_file() and source.suffix.casefold() not in IMAGE_EXTENSIONS | ARCHIVE_EXTENSIONS:
        raise ValueError("Yalnızca klasör, ZIP, CBZ, JPG, PNG ve WebP desteklenir.")

    title = _safe_name(source.stem if source.is_file() else source.name, "Yerel Manga")
    token = uuid.uuid4().hex
    manga_id = f"local_manga_{token}"
    local_root = Path(LOCAL_MANGA_DIR)
    temporary = local_root / f".import-{token}"
    final = local_root / f"{_safe_name(title, 'manga')}-{token[:8]}"
    temporary.mkdir(parents=True, exist_ok=False)
    chapters: list[dict] = []

    try:
        _check_cancelled(cancel_event)
        if source.is_dir():
            definitions = _folder_chapters(source)
            if not definitions:
                raise ValueError("Seçilen klasörde desteklenen manga sayfası bulunamadı.")
            progress_state = {"current": 0, "total": sum(len(pages) for _title, pages in definitions), "bytes": 0}
            for index, (chapter_title, pages) in enumerate(definitions, start=1):
                chapter_dir = temporary / f"chapter-{index:04d}"
                names = _copy_folder_chapter(
                    pages,
                    chapter_dir,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                    progress_state=progress_state,
                )
                chapters.append({"id": f"local_chapter_{token}_{index}", "title": chapter_title, "pages": names})
        elif source.suffix.casefold() in ARCHIVE_EXTENSIONS:
            with zipfile.ZipFile(source) as archive:
                images = _validated_archive_images(archive)
                if not images:
                    raise ValueError("Arşivde desteklenen manga sayfası bulunamadı.")
                progress_state = {"current": 0, "total": len(images), "bytes": 0}
                for index, (chapter_title, items) in enumerate(_archive_groups(images), start=1):
                    chapter_dir = temporary / f"chapter-{index:04d}"
                    chapter_dir.mkdir(parents=True)
                    names = []
                    for page_index, info in enumerate(items, start=1):
                        _check_cancelled(cancel_event)
                        suffix = PurePosixPath(info.filename).suffix.casefold()
                        name = f"{page_index:05d}{suffix}"
                        with archive.open(info) as input_file, (chapter_dir / name).open("wb") as output_file:
                            while chunk := input_file.read(1024 * 1024):
                                _check_cancelled(cancel_event)
                                output_file.write(chunk)
                        names.append(name)
                        progress_state["current"] += 1
                        if progress_callback:
                            progress_callback(progress_state["current"], progress_state["total"], "Arşiv açılıyor…")
                    chapters.append({"id": f"local_chapter_{token}_{index}", "title": chapter_title, "pages": names})
        else:
            chapter_dir = temporary / "chapter-0001"
            progress_state = {"current": 0, "total": 1, "bytes": 0}
            names = _copy_folder_chapter(
                [source], chapter_dir,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                progress_state=progress_state,
            )
            chapters.append({"id": f"local_chapter_{token}_1", "title": "Bölüm 1", "pages": names})

        _check_cancelled(cancel_event)
        if progress_callback:
            progress_callback(progress_state["total"], progress_state["total"], "Kapak hazırlanıyor…")
        first_page = temporary / "chapter-0001" / chapters[0]["pages"][0]
        cover = temporary / "cover.webp"
        _create_cover_thumbnail(first_page, cover)
        _check_cancelled(cancel_event)
        temporary.rename(final)
        for index, chapter in enumerate(chapters, start=1):
            chapter["path"] = str(final / f"chapter-{index:04d}")
        manga = library_manager.add_local_manga(
            manga_id=manga_id,
            title=title,
            cover_path=str(final / cover.name),
            folder_name="",
            chapters=chapters,
        )
        if progress_callback:
            progress_callback(progress_state["total"], progress_state["total"], "Kütüphaneye eklendi.")
        return {"status": "success", "manga": manga, "chapter_count": len(chapters)}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        shutil.rmtree(final, ignore_errors=True)
        raise
