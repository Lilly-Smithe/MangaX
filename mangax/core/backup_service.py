"""Reader ve Full için yerel yedekleme servisi."""
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from mangax.core.config import APP_VERSION, BACKUPS_DIR, DATA_DIR, IS_FULL_EDITION
from mangax.core.dependencies import library_manager
from mangax.core.database import db
from mangax.core.models import LIBRARY_STATUS_VALUES
BACKUP_SCHEMA_VERSION = 1
LOCAL_BACKUP_SETTINGS_FILE = Path(DATA_DIR) / 'backup_settings.json'
LOCAL_BACKUP_FILENAME = re.compile('^mangax-auto-\\d{8}-\\d{6}-\\d{6}\\.json$')
DEFAULT_LOCAL_BACKUP_SETTINGS = {'enabled': True, 'interval_minutes': 30, 'retention_count': 5, 'client_settings': {}}
_local_backup_lock = threading.RLock()
PORTABLE_MANGA_FIELDS = ('id', 'title', 'description', 'cover_url', 'status', 'tags', 'year', 'last_read_chapter', 'last_read_page', 'last_read_chapter_num', 'last_read_chapter_title', 'last_read_source_id', 'last_read_language', 'last_read_online', 'last_read_at', 'last_read_offset', 'last_read_percent', 'library_status', 'user_rating', 'personal_note', 'collections', 'known_chapters', 'unread_count', 'updated_at', 'tracking_enabled', 'tracking_notifications', 'tracking_auto_download', 'tracking_source_manga_id', 'tracking_source_name', 'tracking_last_checked_at', 'tracking_last_error', 'mal_id', 'mal_status', 'mal_num_chapters_read', 'mal_num_volumes_read', 'mal_remote_score', 'mal_last_synced_at', 'mal_remote_updated_at', 'mal_sync_error', 'anilist_id', 'external_titles')

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def export_portable_library(library_manager) -> list[dict]:
    mangas = library_manager.get_library().get('mangas', {})
    portable = []
    for manga in mangas.values():
        row = {field: manga.get(field) for field in PORTABLE_MANGA_FIELDS}
        row['tags'] = list(manga.get('tags') or [])
        row['collections'] = list(manga.get('collections') or [])
        row['known_chapters'] = list(manga.get('known_chapters') or [])
        row['external_titles'] = list(manga.get('external_titles') or [])
        row['downloaded_chapter_count'] = len(manga.get('downloaded_chapters') or {})
        portable.append(row)
    return sorted(portable, key=lambda item: str(item.get('title') or '').casefold())

def import_portable_library(library_manager, mangas: list[dict]) -> dict:
    imported = 0
    history = 0
    skipped = 0
    with db.get_connection() as conn:
        for raw in mangas or []:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            manga_id = str(raw.get('id') or '').strip()
            if not manga_id:
                skipped += 1
                continue
            tags = raw.get('tags') if isinstance(raw.get('tags'), list) else []
            collections = raw.get('collections') if isinstance(raw.get('collections'), list) else []
            known_chapters = raw.get('known_chapters') if isinstance(raw.get('known_chapters'), list) else []
            library_status = str(raw.get('library_status') or 'reading')
            if library_status not in LIBRARY_STATUS_VALUES:
                library_status = 'reading'
            values = {'id': manga_id, 'title': str(raw.get('title') or 'Bilinmeyen Manga'), 'description': str(raw.get('description') or ''), 'cover_url': str(raw.get('cover_url') or ''), 'status': str(raw.get('status') or 'ongoing'), 'tags': json.dumps(tags, ensure_ascii=False), 'year': max(0, int(raw.get('year') or 0)), 'last_read_chapter': str(raw.get('last_read_chapter') or ''), 'last_read_page': max(0, int(raw.get('last_read_page') or 0)), 'last_read_chapter_num': str(raw.get('last_read_chapter_num') or ''), 'last_read_chapter_title': str(raw.get('last_read_chapter_title') or ''), 'last_read_source_id': str(raw.get('last_read_source_id') or ''), 'last_read_language': 'en' if raw.get('last_read_language') == 'en' else 'tr', 'last_read_online': bool(raw.get('last_read_online', True)), 'last_read_at': max(0, int(raw.get('last_read_at') or 0)), 'last_read_offset': max(0.0, min(1.0, float(raw.get('last_read_offset') or 0))), 'last_read_percent': max(0.0, min(1.0, float(raw.get('last_read_percent') or 0))), 'library_status': library_status, 'user_rating': max(0, min(10, int(raw.get('user_rating') or 0))), 'personal_note': str(raw.get('personal_note') or '')[:4000], 'collections': json.dumps(collections[:20], ensure_ascii=False), 'known_chapters': json.dumps(known_chapters, ensure_ascii=False), 'unread_count': max(0, int(raw.get('unread_count') or 0)), 'updated_at': max(0, int(raw.get('updated_at') or 0)), 'tracking_enabled': bool(raw.get('tracking_enabled', False)), 'tracking_notifications': bool(raw.get('tracking_notifications', True)), 'tracking_auto_download': bool(raw.get('tracking_auto_download', False)), 'tracking_source_manga_id': str(raw.get('tracking_source_manga_id') or ''), 'tracking_source_name': str(raw.get('tracking_source_name') or '')[:120], 'tracking_last_checked_at': max(0, int(raw.get('tracking_last_checked_at') or 0)), 'tracking_last_error': str(raw.get('tracking_last_error') or '')[:500], 'mal_id': max(0, int(raw.get('mal_id') or 0)), 'mal_status': str(raw.get('mal_status') or '')[:30], 'mal_num_chapters_read': max(0, int(raw.get('mal_num_chapters_read') or 0)), 'mal_num_volumes_read': max(0, int(raw.get('mal_num_volumes_read') or 0)), 'mal_remote_score': max(0, min(10, int(raw.get('mal_remote_score') or 0))), 'mal_last_synced_at': max(0, int(raw.get('mal_last_synced_at') or 0)), 'mal_remote_updated_at': str(raw.get('mal_remote_updated_at') or '')[:80], 'mal_sync_error': str(raw.get('mal_sync_error') or '')[:500], 'anilist_id': max(0, int(raw.get('anilist_id') or 0)), 'external_titles': json.dumps([str(value)[:300] for value in raw.get('external_titles') or [] if str(value or '').strip()][:50], ensure_ascii=False)}
            exists = conn.execute('SELECT id FROM mangas WHERE id = ?', (manga_id,)).fetchone()
            if exists:
                conn.execute('\n                    UPDATE mangas SET title = ?, description = ?, cover_url = ?, status = ?,\n                        tags = ?, year = ?, last_read_chapter = ?, last_read_page = ?,\n                        last_read_chapter_num = ?, last_read_chapter_title = ?,\n                        last_read_source_id = ?, last_read_language = ?, last_read_online = ?,\n                        last_read_at = ?, last_read_offset = ?, last_read_percent = ?,\n                        library_status = ?, user_rating = ?, personal_note = ?, collections = ?,\n                        known_chapters = ?, unread_count = ?, updated_at = ?,\n                        tracking_enabled = ?, tracking_notifications = ?, tracking_auto_download = ?,\n                        tracking_source_manga_id = ?, tracking_source_name = ?,\n                        tracking_last_checked_at = ?, tracking_last_error = ?,\n                        mal_id = ?, mal_status = ?, mal_num_chapters_read = ?,\n                        mal_num_volumes_read = ?, mal_remote_score = ?, mal_last_synced_at = ?,\n                        mal_remote_updated_at = ?, mal_sync_error = ?,\n                        anilist_id = ?, external_titles = ?\n                    WHERE id = ?\n                ', (values['title'], values['description'], values['cover_url'], values['status'], values['tags'], values['year'], values['last_read_chapter'], values['last_read_page'], values['last_read_chapter_num'], values['last_read_chapter_title'], values['last_read_source_id'], values['last_read_language'], values['last_read_online'], values['last_read_at'], values['last_read_offset'], values['last_read_percent'], values['library_status'], values['user_rating'], values['personal_note'], values['collections'], values['known_chapters'], values['unread_count'], values['updated_at'], values['tracking_enabled'], values['tracking_notifications'], values['tracking_auto_download'], values['tracking_source_manga_id'], values['tracking_source_name'], values['tracking_last_checked_at'], values['tracking_last_error'], values['mal_id'], values['mal_status'], values['mal_num_chapters_read'], values['mal_num_volumes_read'], values['mal_remote_score'], values['mal_last_synced_at'], values['mal_remote_updated_at'], values['mal_sync_error'], values['anilist_id'], values['external_titles'], manga_id))
            else:
                columns = ', '.join(PORTABLE_MANGA_FIELDS)
                placeholders = ', '.join(('?' for _ in PORTABLE_MANGA_FIELDS))
                conn.execute(f'INSERT INTO mangas ({columns}) VALUES ({placeholders})', tuple((values[field] for field in PORTABLE_MANGA_FIELDS)))
            imported += 1
            if values['last_read_chapter']:
                history += 1
        conn.commit()
    return {'mangas_imported': imported, 'history_imported': history, 'skipped': skipped}

def build_backup_payload(client_settings: dict | None=None) -> dict:
    from mangax.core.preferences_manager import preferences_manager
    extensions = []
    source_preferences = []
    custom_sources = []
    source_priority = []
    tracker_settings = {}
    library = export_portable_library(library_manager)
    return {'schema_version': BACKUP_SCHEMA_VERSION, 'app_version': APP_VERSION, 'exported_at': _utc_now(), 'library': library, 'reading_history_count': sum((bool(item.get('last_read_chapter')) for item in library)), 'installed_extensions': extensions, 'custom_sources': custom_sources, 'source_preferences': source_preferences, 'source_priority': source_priority, 'app_preferences': preferences_manager.get_all(), 'client_settings': dict(client_settings or {}), 'chapter_tracker_settings': tracker_settings, 'notes': 'İndirilen manga görsel dosyaları bu taşınabilir yedeğe dahil değildir.'}

def validate_backup_payload(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('Yedek dosyası bir JSON nesnesi olmalı.')
    if int(payload.get('schema_version') or 0) != BACKUP_SCHEMA_VERSION:
        raise ValueError('Bu yedek sürümü MangaX tarafından desteklenmiyor.')
    if not isinstance(payload.get('library', []), list):
        raise ValueError('Yedekteki kütüphane verisi geçersiz.')
    return payload

def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)

def normalize_local_backup_settings(raw: Any) -> dict:
    data = raw if isinstance(raw, dict) else {}
    interval = int(data.get('interval_minutes') or 30)
    retention = int(data.get('retention_count') or 5)
    return {'enabled': bool(data.get('enabled', True)), 'interval_minutes': interval if interval in {15, 30, 60, 180} else 30, 'retention_count': retention if retention in {5, 10} else 5, 'client_settings': dict(data.get('client_settings') or {})}

def load_local_backup_settings() -> dict:
    try:
        raw = json.loads(LOCAL_BACKUP_SETTINGS_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        raw = DEFAULT_LOCAL_BACKUP_SETTINGS
    return normalize_local_backup_settings(raw)

def save_local_backup_settings(settings: dict) -> dict:
    normalized = normalize_local_backup_settings(settings)
    with _local_backup_lock:
        _atomic_write_json(LOCAL_BACKUP_SETTINGS_FILE, normalized)
    return normalized

def _local_backup_path(backup_id: str) -> Path:
    if not LOCAL_BACKUP_FILENAME.fullmatch(str(backup_id or '')):
        raise ValueError('Geçersiz yerel yedek kimliği.')
    return Path(BACKUPS_DIR) / backup_id

def _backup_filename(now: datetime | None=None) -> str:
    value = now or datetime.now(timezone.utc)
    return f"mangax-auto-{value.strftime('%Y%m%d-%H%M%S-%f')}.json"

def list_local_backups() -> list[dict]:
    backup_dir = Path(BACKUPS_DIR)
    if not backup_dir.is_dir():
        return []
    items = []
    for path in sorted(backup_dir.glob('mangax-auto-*.json'), reverse=True):
        if not LOCAL_BACKUP_FILENAME.fullmatch(path.name):
            continue
        try:
            payload = validate_backup_payload(json.loads(path.read_text(encoding='utf-8')))
            metadata = payload.get('local_backup') or {}
            items.append({'id': path.name, 'created_at': payload.get('exported_at') or '', 'reason': str(metadata.get('reason') or 'scheduled'), 'manga_count': len(payload.get('library') or []), 'reading_history_count': int(payload.get('reading_history_count') or 0), 'size_bytes': path.stat().st_size})
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return items

def prune_local_backups(retention_count: int | None=None) -> list[str]:
    keep = retention_count or load_local_backup_settings()['retention_count']
    paths = [Path(BACKUPS_DIR) / item['id'] for item in list_local_backups()]
    removed = []
    for path in paths[keep:]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            pass
    return removed

def create_local_backup(reason: str='scheduled', client_settings: dict | None=None) -> dict:
    with _local_backup_lock:
        settings = load_local_backup_settings()
        saved_client_settings = dict(client_settings) if isinstance(client_settings, dict) else settings['client_settings']
        payload = build_backup_payload(saved_client_settings)
        payload['local_backup'] = {'reason': str(reason or 'scheduled')}
        destination = _local_backup_path(_backup_filename())
        _atomic_write_json(destination, payload)
        prune_local_backups(settings['retention_count'])
        return next((item for item in list_local_backups() if item['id'] == destination.name))

def read_local_backup(backup_id: str) -> dict:
    path = _local_backup_path(backup_id)
    if not path.is_file():
        raise FileNotFoundError('Seçilen yerel yedek artık bulunamıyor.')
    return validate_backup_payload(json.loads(path.read_text(encoding='utf-8')))

class LocalBackupManager:

    def __init__(self):
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self.last_backup_at = ''
        self.last_error = ''

    def start(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(target=self._run, name='MangaXLocalBackup', daemon=True)
            self._thread.start()

    def stop(self, create_final: bool=True) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        if create_final and load_local_backup_settings()['enabled']:
            self.create('shutdown')

    def update_settings(self, settings: dict) -> dict:
        saved = save_local_backup_settings(settings)
        self._wake_event.set()
        return saved

    def create(self, reason: str='scheduled', client_settings: dict | None=None) -> dict | None:
        try:
            item = create_local_backup(reason, client_settings)
            self.last_backup_at = item['created_at']
            self.last_error = ''
            return item
        except Exception as error:
            self.last_error = str(error)
            print(f'[MangaX] Yerel yedek oluşturulamadı: {error}')
            return None

    def status(self) -> dict:
        return {'running': bool(self._thread and self._thread.is_alive()), 'last_backup_at': self.last_backup_at, 'last_error': self.last_error}

    def _run(self) -> None:
        while not self._stop_event.is_set():
            settings = load_local_backup_settings()
            if not settings['enabled']:
                self._wake_event.wait()
                self._wake_event.clear()
                continue
            interrupted = self._wake_event.wait(settings['interval_minutes'] * 60)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            if interrupted:
                continue
            self.create('scheduled')
local_backup_manager = LocalBackupManager()
