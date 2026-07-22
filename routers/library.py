# routers/library.py
# Kütüphane endpoint'leri

from fastapi import APIRouter, BackgroundTasks, HTTPException
from mangax.core.models import (
    DeleteRequest,
    KnownChaptersRequest,
    LibraryBulkDeleteRequest,
    LibraryBulkUpdateRequest,
    LibraryMetadataRequest,
    ProgressRequest,
    ReaderBookmarkRequest,
    ReaderProfileRequest,
)
from mangax.core.dependencies import library_manager
from mangax.integrations.mal_outbound import mal_outbound_service

router = APIRouter(prefix="/api", tags=["Library"])


@router.get("/library")
def get_library():
    """Yerel kütüphaneyi getir"""
    return library_manager.get_library(include_storage=False)


@router.post("/library/maintenance")
def schedule_library_maintenance(background_tasks: BackgroundTasks):
    """Pahalı dosya ve metadata onarımlarını yanıt yolunun dışında çalıştır."""
    background_tasks.add_task(library_manager.run_background_maintenance)
    return {"status": "scheduled"}


@router.post("/progress")
def update_progress(req: ProgressRequest):
    """Okuma ilerlemesini güncelle"""
    before = library_manager.get_manga(req.manga_id)
    manga = library_manager.update_progress(
        req.manga_id,
        req.chapter_id,
        req.page_index,
        manga_title=req.manga_title,
        description=req.description,
        cover_url=req.cover_url,
        status=req.status,
        chapter_num=req.chapter_num,
        chapter_title=req.chapter_title,
        source_id=req.source_id,
        language=req.language,
        online=req.online,
        page_offset=req.page_offset,
        chapter_percent=req.chapter_percent,
    )
    mal_outbound_service.enqueue_local_change(before, manga)
    return {"status": "success", "manga": manga}


@router.post("/delete")
def delete_chapter(req: DeleteRequest):
    """İndirilmiş bölümü sil"""
    success = library_manager.remove_downloaded_chapter(
        req.manga_id, req.chapter_id
    )
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Bölüm silinemedi veya bulunamadı."
        )
    return {"status": "success", "message": "Bölüm silindi."}


@router.delete("/library/{manga_id}")
def delete_manga(manga_id: str):
    """Bir serinin tüm indirmelerini ve kütüphane kaydını sil."""
    success = library_manager.remove_manga(manga_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Seri silinemedi veya bulunamadı."
        )
    return {"status": "success", "message": "Seri tamamen silindi."}


@router.post("/library/bulk-delete")
def bulk_delete_library(req: LibraryBulkDeleteRequest):
    """Seçilen serileri mevcut güvenli dosya ve ilişki temizliğiyle kaldır."""
    result = library_manager.remove_mangas(req.manga_ids)
    return {
        "status": "success" if not result["failed_ids"] else "partial",
        "removed": len(result["removed_ids"]),
        **result,
    }


@router.put("/library/{manga_id}/metadata")
def update_library_metadata(manga_id: str, req: LibraryMetadataRequest):
    before = library_manager.get_manga(manga_id)
    manga = library_manager.update_library_metadata(
        manga_id,
        library_status=req.library_status,
        user_rating=req.user_rating,
        personal_note=req.personal_note,
        collections=req.collections,
        mal_num_chapters_read=req.mal_num_chapters_read,
        mal_num_volumes_read=req.mal_num_volumes_read,
    )
    if not manga:
        raise HTTPException(status_code=404, detail="Kütüphane kaydı bulunamadı.")
    mal_outbound_service.enqueue_local_change(before, manga)
    return {"status": "success", "manga": manga}


@router.put("/library/bulk-update")
def bulk_update_library(req: LibraryBulkUpdateRequest):
    before = {
        manga_id: library_manager.get_manga(manga_id)
        for manga_id in req.manga_ids
    }
    mangas = library_manager.bulk_update_library(
        req.manga_ids,
        library_status=req.library_status,
        add_collection=req.add_collection,
    )
    for manga in mangas:
        mal_outbound_service.enqueue_local_change(before.get(manga["id"]), manga)
    return {"status": "success", "updated": len(mangas), "mangas": mangas}


@router.post("/library/{manga_id}/known-chapters")
def update_known_chapters(manga_id: str, req: KnownChaptersRequest):
    manga = library_manager.update_known_chapters(manga_id, req.chapter_numbers)
    if not manga:
        raise HTTPException(status_code=404, detail="Kütüphane kaydı bulunamadı.")
    return {"status": "success", "manga": manga}


@router.put("/library/{manga_id}/reader-profile")
def update_reader_profile(manga_id: str, req: ReaderProfileRequest):
    manga = library_manager.update_reader_profile(manga_id, req.model_dump())
    if not manga:
        raise HTTPException(status_code=404, detail="Kütüphane kaydı bulunamadı.")
    return {"status": "success", "manga": manga}


@router.post("/library/{manga_id}/bookmarks")
def add_reader_bookmark(manga_id: str, req: ReaderBookmarkRequest):
    manga = library_manager.add_page_bookmark(manga_id, req.model_dump())
    if not manga:
        raise HTTPException(status_code=404, detail="Kütüphane kaydı bulunamadı.")
    return {"status": "success", "bookmarks": manga.get("page_bookmarks", []), "manga": manga}


@router.delete("/library/{manga_id}/bookmarks/{chapter_id}/{page_index}")
def delete_reader_bookmark(manga_id: str, chapter_id: str, page_index: int):
    manga = library_manager.remove_page_bookmark(manga_id, chapter_id, page_index)
    if not manga:
        raise HTTPException(status_code=404, detail="Kütüphane kaydı bulunamadı.")
    return {"status": "success", "bookmarks": manga.get("page_bookmarks", []), "manga": manga}
