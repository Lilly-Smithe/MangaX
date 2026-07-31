from pathlib import Path
from typing import Any
import time
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from mangax.core.config import APP_EDITION, APP_VERSION, DATA_DIR, DOWNLOADS_DIR, IS_FULL_EDITION
from mangax.core.dependencies import library_manager
from mangax.core.preferences_manager import preferences_manager
router = APIRouter(prefix='/api/preferences', tags=['Preferences'])

class PreferencesUpdate(BaseModel):
    app_theme: str | None = None
    pornhub_theme_unlocked: bool | None = None
    nav_position: str | None = None
    nav_mode: str | None = None
    nav_auto_hide: bool | None = None
    library_density: str | None = None
    reduce_motion: bool | None = None
    ui_scale: str | None = None
    request_timeout_seconds: int | None = Field(default=None, ge=5, le=60)
    download_concurrency: int | None = Field(default=None, ge=1, le=8)
    low_bandwidth_mode: bool | None = None
    image_cache_limit_mb: int | None = Field(default=None, ge=64, le=4096)
    download_directory: str | None = Field(default=None, max_length=500)
    safe_mode: bool | None = None
    extension_update_mode: str | None = None
    backup_before_extension_update: bool | None = None
    fallback_mode: str | None = None
    catalog_provider_preference: str | None = None
    automatic_update_checks: bool | None = None
    source_priority: list[str] | None = Field(default=None, max_length=100)

class ReleaseNotesSeenRequest(BaseModel):
    version: str = Field(min_length=1, max_length=40)

def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0

def _cache_size() -> int:
    return sum((_file_size(path) for path in (Path(DATA_DIR) / 'anilist_cache.json', Path(DATA_DIR) / 'news_cache.json')))

@router.get('')
def get_preferences() -> dict[str, Any]:
    library = library_manager.get_library().get('mangas', {})
    storage = [{'id': manga_id, 'title': manga.get('title') or manga_id, 'bytes': int(manga.get('storage_bytes') or 0)} for manga_id, manga in library.items() if int(manga.get('storage_bytes') or 0) > 0]
    storage.sort(key=lambda item: item['bytes'], reverse=True)
    source_priority = []
    sources = []
    settings = preferences_manager.get_all()
    settings.pop('catalog_provider_preference', None)
    return {'settings': settings, 'source_priority': source_priority, 'sources': sources, 'storage': {'downloads_directory': DOWNLOADS_DIR, 'cache_bytes': _cache_size(), 'mangas': storage, 'total_download_bytes': sum((item['bytes'] for item in storage))}}

@router.get('/startup')
def get_startup_experience(legacy_onboarding_completed: bool=Query(default=False)) -> dict[str, Any]:
    library = library_manager.get_library().get('mangas', {})
    return preferences_manager.startup_experience(current_version=APP_VERSION, edition=APP_EDITION, has_existing_data=bool(library), legacy_completed=legacy_onboarding_completed)

@router.post('/onboarding/complete')
def complete_onboarding() -> dict[str, Any]:
    preferences_manager.complete_onboarding(APP_VERSION)
    return {'status': 'success', 'onboarding_completed': True, 'version': APP_VERSION}

@router.post('/release-notes/seen')
def mark_release_notes_seen(request: ReleaseNotesSeenRequest) -> dict[str, Any]:
    if request.version != APP_VERSION:
        raise HTTPException(status_code=409, detail='Sürüm notu uygulama sürümüyle eşleşmiyor.')
    preferences_manager.mark_release_notes_seen(request.version)
    return {'status': 'success', 'version': request.version}

@router.put('')
def update_preferences(request: PreferencesUpdate) -> dict[str, Any]:
    values = request.model_dump(exclude_none=True)
    values.pop('catalog_provider_preference', None)
    priority = values.pop('source_priority', None)
    previous_catalog_provider = str(preferences_manager.get_all().get('catalog_provider_preference') or 'anilist')
    try:
        settings = preferences_manager.update(values)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error))
    source_priority = []
    settings.pop('catalog_provider_preference', None)
    return {'status': 'success', 'settings': settings, 'source_priority': source_priority}

@router.post('/cache/clear')
def clear_cache() -> dict[str, Any]:
    cleared = 0
    for path in (Path(DATA_DIR) / 'anilist_cache.json', Path(DATA_DIR) / 'news_cache.json'):
        try:
            if path.is_file():
                cleared += path.stat().st_size
                path.unlink()
        except OSError as error:
            print(f'[Preferences] cache_cleanup_failed: {type(error).__name__}', flush=True)
            raise HTTPException(status_code=500, detail='Önbellek temizlenemedi.') from error
    return {'status': 'success', 'cleared_bytes': cleared}

@router.post('/storage/cleanup-stale')
def cleanup_stale_downloads() -> dict[str, Any]:
    cutoff = int(time.time()) - 30 * 24 * 60 * 60
    library = library_manager.get_library().get('mangas', {})
    removed_chapters = 0
    freed_bytes = 0
    for manga_id, manga in library.items():
        last_activity = max(int(manga.get('last_read_at') or 0), int(manga.get('updated_at') or 0))
        if last_activity >= cutoff:
            continue
        freed_bytes += int(manga.get('storage_bytes') or 0)
        for chapter_id in list((manga.get('downloaded_chapters') or {}).keys()):
            if library_manager.remove_downloaded_chapter(manga_id, chapter_id):
                removed_chapters += 1
    return {'status': 'success', 'removed_chapters': removed_chapters, 'freed_bytes': freed_bytes}

@router.post('/reset')
def reset_preferences() -> dict[str, Any]:
    settings = preferences_manager.reset()
    return {'status': 'success', 'settings': settings}
