"""Edition bağımsız yerel bölüm/sayfa sunumu."""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from core_dependencies import library_manager


router = APIRouter(prefix="/api/local", tags=["Local Reader"])


@router.get("/manga/{manga_id}/chapters/{chapter_id}/pages")
def get_local_chapter_pages(manga_id: str, chapter_id: str) -> dict:
    manga = library_manager.get_manga(manga_id)
    if not manga:
        raise HTTPException(status_code=404, detail="Manga kütüphanede bulunamadı.")

    chapter = (manga.get("downloaded_chapters") or {}).get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Yerel bölüm bulunamadı.")

    chapter_url = library_manager.managed_file_url(str(chapter.get("path") or "")).rstrip("/")
    if chapter_url:
        page_urls = [
            f"{quote(chapter_url, safe='/')}/{quote(str(page), safe='')}"
            for page in chapter.get("pages") or []
        ]
    else:
        page_urls = [
            f"/downloads/{quote(manga_id, safe='')}/{quote(chapter_id, safe='')}/{quote(str(page), safe='')}"
            for page in chapter.get("pages") or []
        ]

    return {"chapter_id": chapter_id, "pages": page_urls, "online": False}
