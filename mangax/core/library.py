"""Reader ve Full tarafından paylaşılan kütüphane servisi."""
import sqlite3
import os
import json
import shutil
import stat
import threading
import time
import re
from typing import Dict, Any, Optional, List
from mangax.core.config import BASE_DIR, DATA_DIR, DOWNLOADS_DIR, LOCAL_MANGA_DIR
from mangax.core.database import db
from mangax.core.models import LIBRARY_STATUS_VALUES
LIBRARY_FILE = os.path.join(DATA_DIR, 'library.json')
BACKUP_FILE = os.path.join(DATA_DIR, 'library.json.bak')

class LibraryManager:

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        self._repair_lock = threading.Lock()
        self._migrate_from_json_if_needed()

    def run_background_maintenance(self) -> bool:
        """Kütüphane onarımlarını normal okuma yolunu engellemeden tek seferde çalıştır."""
        if not self._repair_lock.acquire(blocking=False):
            return False
        try:
            for repair in (self.repair_missing_download_records, self.repair_missing_metadata, self.repair_missing_covers):
                try:
                    repair()
                except Exception as error:
                    print(f'Error during background library maintenance: {error}')
            return True
        finally:
            self._repair_lock.release()

    def _migrate_from_json_if_needed(self):
        if os.path.exists(LIBRARY_FILE):
            try:
                with open(LIBRARY_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                mangas = data.get('mangas', {})
                if not mangas:
                    os.rename(LIBRARY_FILE, BACKUP_FILE)
                    return
                with db.get_connection() as conn:
                    cur = conn.execute('SELECT COUNT(*) FROM mangas')
                    if cur.fetchone()[0] == 0:
                        print('[Migration] Migrating library.json to library.db...')
                        for manga_id, manga in mangas.items():
                            tags = json.dumps(manga.get('tags', []))
                            conn.execute('\n                                INSERT INTO mangas (\n                                    id, title, description, cover_url, cover_path, status,\n                                    folder_name, tags, year, last_read_chapter, last_read_page,\n                                    last_read_chapter_num, last_read_chapter_title, last_read_source_id,\n                                    last_read_language, last_read_online, last_read_at\n                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                            ', (manga.get('id', manga_id), manga.get('title', ''), manga.get('description', ''), manga.get('cover_url', ''), manga.get('cover_path', ''), manga.get('status', 'unknown'), manga.get('folder_name', ''), tags, manga.get('year', 0) or 0, manga.get('last_read_chapter', ''), manga.get('last_read_page', 0), manga.get('last_read_chapter_num', ''), manga.get('last_read_chapter_title', ''), manga.get('last_read_source_id', ''), manga.get('last_read_language', 'tr'), bool(manga.get('last_read_online', True)), manga.get('last_read_at', 0)))
                            chapters = manga.get('downloaded_chapters', {})
                            for ch_id, ch in chapters.items():
                                pages = json.dumps(ch.get('pages', []))
                                conn.execute('\n                                    INSERT INTO downloaded_chapters (\n                                        id, manga_id, chapter, title, language, pages, path\n                                    ) VALUES (?, ?, ?, ?, ?, ?, ?)\n                                ', (ch.get('id', ch_id), manga_id, ch.get('chapter', ''), ch.get('title', ''), ch.get('language', 'tr'), pages, ch.get('path', '')))
                        conn.commit()
                        print('[Migration] Successfully migrated JSON to SQLite.')
                os.rename(LIBRARY_FILE, BACKUP_FILE)
            except Exception as e:
                print(f'Error migrating library.json: {e}')

    def add_manga(self, manga_id: str, title: str, description: str, cover_url: str, status: str) -> Dict[str, Any]:
        updated_at = int(time.time())
        with db.get_connection() as conn:
            cur = conn.execute('SELECT id FROM mangas WHERE id = ?', (manga_id,))
            if not cur.fetchone():
                conn.execute("\n                    INSERT INTO mangas (\n                        id, title, description, cover_url, cover_path, status,\n                        folder_name, tags, year, last_read_chapter, last_read_page,\n                        last_read_chapter_num, last_read_chapter_title, last_read_source_id,\n                        last_read_language, last_read_online, last_read_at, updated_at\n                    ) VALUES (?, ?, ?, ?, '', ?, '', '[]', 0, '', 0, '', '', '', 'tr', 1, 0, ?)\n                ", (manga_id, title, description, cover_url, status, updated_at))
            else:
                conn.execute('\n                    UPDATE mangas \n                    SET title = ?, description = ?, cover_url = ?, status = ?, updated_at = ?\n                    WHERE id = ?\n                ', (title, description, cover_url, status, updated_at, manga_id))
            conn.commit()
        return self.get_manga(manga_id) or {}

    def import_mal_entry(self, manga: Dict[str, Any], mal_entry: Dict[str, Any]) -> Dict[str, Any]:
        """Onaylanmış bir MAL kaydını yerel kütüphaneye tek yönlü olarak ekle/güncelle."""
        manga_id = str(manga.get('id') or '')
        if not manga_id.startswith(('anilist_', 'mal_')):
            raise ValueError('MAL kaydı güvenilir bir manga kimliğiyle eşleşmedi')
        mal_id = max(0, int(mal_entry.get('mal_id') or 0))
        if not mal_id:
            raise ValueError('Geçersiz MAL kimliği')
        mal_status = str(mal_entry.get('status') or 'plan_to_read')
        status_map = {'reading': 'reading', 'completed': 'completed', 'on_hold': 'on_hold', 'dropped': 'dropped', 'plan_to_read': 'plan_to_read'}
        library_status = status_map.get(mal_status, 'on_hold')
        self.add_manga(manga_id, str(manga.get('title') or mal_entry.get('title') or 'Bilinmeyen Manga'), str(manga.get('description') or ''), str(manga.get('cover_url') or mal_entry.get('cover_url') or ''), str(manga.get('status') or 'ongoing'))
        with db.get_connection() as conn:
            row = conn.execute('SELECT collections FROM mangas WHERE id = ?', (manga_id,)).fetchone()
            collections = json.loads((row['collections'] if row else '[]') or '[]')
            collections.append('MyAnimeList')
            if mal_status == 'plan_to_read':
                collections.append('MAL: Okuma Planı')
            conn.execute('\n                UPDATE mangas SET mal_id = ?, mal_status = ?, mal_num_chapters_read = ?,\n                    mal_num_volumes_read = ?, library_status = ?, user_rating = ?,\n                    mal_remote_score = ?,\n                    collections = ?, tags = ?, year = ?, last_read_online = ?, updated_at = ? WHERE id = ?\n            ', (mal_id, mal_status, max(0, int(mal_entry.get('num_chapters_read') or 0)), max(0, int(mal_entry.get('num_volumes_read') or 0)), library_status, max(0, min(10, int(mal_entry.get('score') or 0))), max(0, min(10, int(mal_entry.get('score') or 0))), json.dumps(self._clean_collections(collections), ensure_ascii=False), json.dumps(manga.get('tags') or [], ensure_ascii=False), int(manga.get('year') or 0), 0 if manga_id.startswith('mal_') else 1, int(time.time()), manga_id))
            conn.commit()
        return self.get_manga(manga_id) or {}

    def set_cover_path(self, manga_id: str, cover_path: str):
        rel_path = os.path.relpath(cover_path, BASE_DIR).replace('\\', '/')
        with db.get_connection() as conn:
            conn.execute('UPDATE mangas SET cover_path = ? WHERE id = ?', (rel_path, manga_id))
            conn.commit()

    def set_folder_name(self, manga_id: str, folder_name: str):
        with db.get_connection() as conn:
            cur = conn.execute('SELECT id FROM mangas WHERE id = ?', (manga_id,))
            if not cur.fetchone():
                conn.execute("\n                    INSERT INTO mangas (\n                        id, title, description, cover_url, cover_path, status,\n                        folder_name, tags, year, last_read_chapter, last_read_page,\n                        last_read_chapter_num, last_read_chapter_title, last_read_source_id,\n                        last_read_language, last_read_online, last_read_at\n                    ) VALUES (?, ?, '', '', '', 'unknown', ?, '[]', 0, '', 0, '', '', '', 'tr', 1, 0)\n                ", (manga_id, folder_name, folder_name))
            else:
                conn.execute('UPDATE mangas SET folder_name = ? WHERE id = ?', (folder_name, manga_id))
            conn.commit()

    def add_downloaded_chapter(self, manga_id: str, chapter_id: str, chapter_num: str, title: str, language: str, page_filenames: list, chapter_dir: str | None=None) -> Dict[str, Any]:
        with db.get_connection() as conn:
            cur = conn.execute('SELECT id FROM mangas WHERE id = ?', (manga_id,))
            if not cur.fetchone():
                self.add_manga(manga_id, 'Unknown Manga', '', '', 'unknown')
        if chapter_dir:
            rel_path = os.path.relpath(chapter_dir, BASE_DIR)
        else:
            rel_path = os.path.relpath(os.path.join(DOWNLOADS_DIR, manga_id, chapter_id), BASE_DIR)
        rel_path = rel_path.replace('\\', '/')
        pages_json = json.dumps(page_filenames)
        with db.get_connection() as conn:
            conn.execute('\n                INSERT OR REPLACE INTO downloaded_chapters (\n                    id, manga_id, chapter, title, language, pages, path\n                ) VALUES (?, ?, ?, ?, ?, ?, ?)\n            ', (chapter_id, manga_id, chapter_num, title, language, pages_json, rel_path))
            conn.commit()
            cur = conn.execute('SELECT * FROM downloaded_chapters WHERE id = ? AND manga_id = ?', (chapter_id, manga_id))
            row = cur.fetchone()
            if row:
                return dict(row)
            return {}

    def add_local_manga(self, *, manga_id: str, title: str, cover_path: str, folder_name: str, chapters: list[dict[str, Any]]) -> Dict[str, Any]:
        """Yönetilen yerel dosyaları tek SQLite işlemiyle kütüphaneye kaydet."""
        if not manga_id.startswith('local_manga_'):
            raise ValueError('Geçersiz yerel manga kimliği')
        if not chapters:
            raise ValueError('En az bir yerel bölüm gerekli')
        cover_info = self._managed_path_info(cover_path)
        if not cover_info or cover_info[2] != '/local-manga':
            raise ValueError('Yerel manga kapağı yönetilen yerel manga klasöründe olmalı')
        stored_cover = cover_info[0]
        updated_at = int(time.time())
        known_chapters = [str(index) for index in range(1, len(chapters) + 1)]
        with db.get_connection() as conn:
            conn.execute("\n                INSERT INTO mangas (\n                    id, title, description, cover_url, cover_path, status,\n                    folder_name, tags, year, last_read_chapter, last_read_page,\n                    last_read_chapter_num, last_read_chapter_title, last_read_source_id,\n                    last_read_language, last_read_online, last_read_at,\n                    library_status, known_chapters, unread_count, updated_at\n                ) VALUES (?, ?, '', '', ?, 'local', ?, '[]', 0, '', 0, '', '', '',\n                    'tr', 0, 0, 'reading', ?, ?, ?)\n            ", (manga_id, title, stored_cover, folder_name, json.dumps(known_chapters), len(known_chapters), updated_at))
            for index, chapter in enumerate(chapters, start=1):
                chapter_info = self._managed_path_info(str(chapter['path']))
                if not chapter_info or chapter_info[2] != '/local-manga':
                    raise ValueError('Yerel manga bölümü yönetilen yerel manga klasöründe olmalı')
                stored_path = chapter_info[0]
                conn.execute("\n                    INSERT INTO downloaded_chapters (\n                        id, manga_id, chapter, title, language, pages, path\n                    ) VALUES (?, ?, ?, ?, 'tr', ?, ?)\n                ", (str(chapter['id']), manga_id, str(index), str(chapter.get('title') or f'Bölüm {index}'), json.dumps(list(chapter['pages']), ensure_ascii=False), stored_path))
            conn.commit()
        return self.get_manga(manga_id) or {}

    def update_progress(self, manga_id: str, chapter_id: str, page_index: int, *, manga_title: str='', description: str='', cover_url: str='', status: str='ongoing', chapter_num: str='', chapter_title: str='', source_id: str='', language: str='tr', online: bool=True, page_offset: float=0.0, chapter_percent: float=0.0) -> Dict[str, Any]:
        language = language if language in {'tr', 'en'} else 'tr'
        at_time = int(time.time())
        page_offset = max(0.0, min(1.0, float(page_offset or 0.0)))
        chapter_percent = max(0.0, min(1.0, float(chapter_percent or 0.0)))
        with db.get_connection() as conn:
            cur = conn.execute('SELECT id FROM mangas WHERE id = ?', (manga_id,))
            if not cur.fetchone():
                conn.execute("\n                    INSERT INTO mangas (\n                        id, title, description, cover_url, cover_path, status,\n                        folder_name, tags, year, last_read_chapter, last_read_page,\n                        last_read_chapter_num, last_read_chapter_title, last_read_source_id,\n                        last_read_language, last_read_online, last_read_at,\n                        last_read_offset, last_read_percent, updated_at\n                    ) VALUES (?, ?, ?, ?, '', ?, '', '[]', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                ", (manga_id, manga_title or 'Bilinmeyen Manga', description, cover_url, status or 'ongoing', chapter_id, max(0, int(page_index)), chapter_num, chapter_title, source_id, language, bool(online), at_time, page_offset, chapter_percent, at_time))
            else:
                updates = []
                params = []
                if manga_title:
                    updates.append('title = ?')
                    params.append(manga_title)
                if description:
                    updates.append('description = ?')
                    params.append(description)
                if cover_url:
                    updates.append('cover_url = ?')
                    params.append(cover_url)
                if status:
                    updates.append('status = ?')
                    params.append(status)
                updates.extend(['last_read_chapter = ?', 'last_read_page = ?', 'last_read_chapter_num = ?', 'last_read_chapter_title = ?', 'last_read_source_id = ?', 'last_read_language = ?', 'last_read_online = ?', 'last_read_at = ?'])
                updates.extend(['last_read_offset = ?', 'last_read_percent = ?'])
                updates.append('updated_at = ?')
                params.extend([chapter_id, max(0, int(page_index)), chapter_num, chapter_title, source_id, language, bool(online), at_time, page_offset, chapter_percent, at_time])
                params.append(manga_id)
                query = f"UPDATE mangas SET {', '.join(updates)} WHERE id = ?"
                conn.execute(query, params)
            conn.commit()
        self._refresh_unread_count(manga_id, chapter_num)
        return self.get_manga(manga_id) or {}

    def get_progress(self, manga_id: str) -> Dict[str, Any]:
        with db.get_connection() as conn:
            cur = conn.execute('SELECT last_read_chapter, last_read_page, last_read_offset, last_read_percent FROM mangas WHERE id = ?', (manga_id,))
            row = cur.fetchone()
            if row:
                return {'last_read_chapter': row['last_read_chapter'], 'last_read_page': row['last_read_page'], 'last_read_offset': row['last_read_offset'] or 0, 'last_read_percent': row['last_read_percent'] or 0}
            return {'last_read_chapter': '', 'last_read_page': 0, 'last_read_offset': 0, 'last_read_percent': 0}

    def _row_to_dict(self, manga_row: sqlite3.Row, chapters_rows: List[sqlite3.Row], *, include_pages: bool=True) -> Dict[str, Any]:
        m = dict(manga_row)
        m['tags'] = json.loads(m.get('tags') or '[]')
        m['collections'] = json.loads(m.get('collections') or '[]')
        m['known_chapters'] = json.loads(m.get('known_chapters') or '[]')
        m['external_titles'] = json.loads(m.get('external_titles') or '[]')
        m['last_read_online'] = bool(m.get('last_read_online'))
        m['tracking_enabled'] = bool(m.get('tracking_enabled'))
        m['tracking_notifications'] = bool(m.get('tracking_notifications', 1))
        m['tracking_auto_download'] = bool(m.get('tracking_auto_download'))
        m['cover_local_url'] = self.managed_file_url(m.get('cover_path', ''))
        m['downloaded_chapters'] = {}
        for ch in chapters_rows:
            ch_dict = dict(ch)
            pages = json.loads(ch_dict.get('pages') or '[]')
            ch_dict['page_count'] = len(pages)
            if include_pages:
                ch_dict['pages'] = pages
            else:
                ch_dict.pop('pages', None)
            m['downloaded_chapters'][ch_dict['id']] = ch_dict
        return m

    @staticmethod
    def _clean_collections(values: list) -> list[str]:
        result = []
        seen = set()
        for raw in values or []:
            value = ' '.join(str(raw or '').strip().split())[:40]
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
            if len(result) >= 20:
                break
        return result

    @staticmethod
    def _chapter_number(value: Any) -> float | None:
        match = re.search('\\d+(?:[.,]\\d+)?', str(value or ''))
        if not match:
            return None
        try:
            return float(match.group(0).replace(',', '.'))
        except ValueError:
            return None

    @classmethod
    def _normalized_chapter_numbers(cls, values: list) -> list[str]:
        numbers = {number for number in (cls._chapter_number(value) for value in values or []) if number is not None}
        return [f'{number:g}' for number in sorted(numbers)]

    @classmethod
    def _unread_count(cls, known_chapters: list, last_read_chapter_num: Any) -> int:
        last_read = cls._chapter_number(last_read_chapter_num)
        if last_read is None:
            return len(cls._normalized_chapter_numbers(known_chapters))
        return sum((cls._chapter_number(chapter) > last_read for chapter in cls._normalized_chapter_numbers(known_chapters)))

    def _refresh_unread_count(self, manga_id: str, chapter_num: Any) -> None:
        with db.get_connection() as conn:
            row = conn.execute('SELECT known_chapters FROM mangas WHERE id = ?', (manga_id,)).fetchone()
            if not row:
                return
            known = json.loads(row['known_chapters'] or '[]')
            conn.execute('UPDATE mangas SET unread_count = ? WHERE id = ?', (self._unread_count(known, chapter_num), manga_id))
            conn.commit()

    def update_library_metadata(self, manga_id: str, *, library_status: str, user_rating: int, personal_note: str, collections: list, mal_num_chapters_read: int | None=None, mal_num_volumes_read: int | None=None) -> Dict[str, Any] | None:
        status = library_status if library_status in LIBRARY_STATUS_VALUES else 'reading'
        rating = max(0, min(10, int(user_rating or 0)))
        note = str(personal_note or '')[:4000]
        cleaned_collections = self._clean_collections(collections)
        with db.get_connection() as conn:
            updates = ['library_status = ?', 'user_rating = ?', 'personal_note = ?', 'collections = ?', 'updated_at = ?']
            params: list[Any] = [status, rating, note, json.dumps(cleaned_collections, ensure_ascii=False), int(time.time())]
            if mal_num_chapters_read is not None:
                updates.append('mal_num_chapters_read = ?')
                params.append(max(0, int(mal_num_chapters_read)))
            if mal_num_volumes_read is not None:
                updates.append('mal_num_volumes_read = ?')
                params.append(max(0, int(mal_num_volumes_read)))
            params.append(manga_id)
            result = conn.execute(f"UPDATE mangas SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            if result.rowcount == 0:
                return None
        return self.get_manga(manga_id)

    def bulk_update_library(self, manga_ids: list[str], *, library_status: str | None=None, add_collection: str='') -> list[Dict[str, Any]]:
        ids = list(dict.fromkeys((str(value) for value in manga_ids if str(value).strip())))[:200]
        collection = self._clean_collections([add_collection])
        updated_ids = []
        with db.get_connection() as conn:
            for manga_id in ids:
                row = conn.execute('SELECT collections FROM mangas WHERE id = ?', (manga_id,)).fetchone()
                if not row:
                    continue
                updates = ['updated_at = ?']
                params: list[Any] = [int(time.time())]
                if library_status in LIBRARY_STATUS_VALUES:
                    updates.append('library_status = ?')
                    params.append(library_status)
                if collection:
                    existing = json.loads(row['collections'] or '[]')
                    updates.append('collections = ?')
                    params.append(json.dumps(self._clean_collections([*existing, collection[0]]), ensure_ascii=False))
                params.append(manga_id)
                conn.execute(f"UPDATE mangas SET {', '.join(updates)} WHERE id = ?", params)
                updated_ids.append(manga_id)
            conn.commit()
        return [manga for manga_id in updated_ids if (manga := self.get_manga(manga_id))]

    def update_known_chapters(self, manga_id: str, chapter_numbers: list) -> Dict[str, Any] | None:
        with db.get_connection() as conn:
            row = conn.execute('SELECT known_chapters, last_read_chapter_num FROM mangas WHERE id = ?', (manga_id,)).fetchone()
            if not row:
                return None
            existing = json.loads(row['known_chapters'] or '[]')
            known = self._normalized_chapter_numbers([*existing, *chapter_numbers])
            unread = self._unread_count(known, row['last_read_chapter_num'])
            changed = known != self._normalized_chapter_numbers(existing)
            conn.execute('\n                UPDATE mangas SET known_chapters = ?, unread_count = ?,\n                    updated_at = CASE WHEN ? THEN ? ELSE updated_at END\n                WHERE id = ?\n            ', (json.dumps(known), unread, changed, int(time.time()), manga_id))
            conn.commit()
        return self.get_manga(manga_id)

    def update_tracking_preferences(self, manga_id: str, *, enabled: bool, notifications: bool, auto_download: bool) -> Dict[str, Any] | None:
        with db.get_connection() as conn:
            row = conn.execute('SELECT last_read_source_id, tracking_source_manga_id FROM mangas WHERE id = ?', (manga_id,)).fetchone()
            if not row:
                return None
            source_manga_id = row['tracking_source_manga_id'] or row['last_read_source_id'] or ''
            conn.execute("\n                UPDATE mangas SET tracking_enabled = ?, tracking_notifications = ?,\n                    tracking_auto_download = ?, tracking_source_manga_id = ?,\n                    tracking_last_error = CASE WHEN ? THEN tracking_last_error ELSE '' END\n                WHERE id = ?\n            ", (bool(enabled), bool(notifications), bool(auto_download), source_manga_id, bool(enabled), manga_id))
            conn.commit()
        return self.get_manga(manga_id)

    def get_tracked_mangas(self) -> list[Dict[str, Any]]:
        with db.get_connection() as conn:
            rows = conn.execute('SELECT * FROM mangas WHERE tracking_enabled = 1 ORDER BY title COLLATE NOCASE').fetchall()
            return [self._row_to_dict(row, []) for row in rows]

    def update_tracking_result(self, manga_id: str, *, source_manga_id: str, source_name: str, checked_at: int, error: str='') -> None:
        with db.get_connection() as conn:
            conn.execute('\n                UPDATE mangas SET tracking_source_manga_id = ?, tracking_source_name = ?,\n                    tracking_last_checked_at = ?, tracking_last_error = ? WHERE id = ?\n            ', (str(source_manga_id or ''), str(source_name or '')[:120], max(0, int(checked_at or 0)), str(error or '')[:500], manga_id))
            conn.commit()

    def get_manga(self, manga_id: str) -> Optional[Dict[str, Any]]:
        with db.get_connection() as conn:
            cur = conn.execute('SELECT * FROM mangas WHERE id = ?', (manga_id,))
            manga_row = cur.fetchone()
            if not manga_row:
                return None
            cur = conn.execute('SELECT * FROM downloaded_chapters WHERE manga_id = ?', (manga_id,))
            chapters_rows = cur.fetchall()
            return self._row_to_dict(manga_row, chapters_rows)

    def get_library(self, *, include_storage: bool=True) -> Dict[str, Any]:
        lib = {'mangas': {}}
        with db.get_connection() as conn:
            mangas_rows = conn.execute('SELECT * FROM mangas').fetchall()
            chapters_by_manga: Dict[str, List[sqlite3.Row]] = {}
            for chapter_row in conn.execute('SELECT * FROM downloaded_chapters ORDER BY manga_id').fetchall():
                chapters_by_manga.setdefault(chapter_row['manga_id'], []).append(chapter_row)
            for m_row in mangas_rows:
                m_id = m_row['id']
                manga = self._row_to_dict(m_row, chapters_by_manga.get(m_id, []), include_pages=False)
                if include_storage:
                    manga['storage_bytes'] = self.get_manga_storage_bytes(manga)
                lib['mangas'][m_id] = manga
        return lib

    def get_manga_storage_bytes(self, manga: Dict[str, Any]) -> int:
        """Bir manganın indirme klasörlerini güvenli sınırlar içinde tekilleştirip ölç."""
        roots = set()
        folder_name = manga.get('folder_name', '')
        if folder_name:
            safe_root = self._safe_download_path(os.path.join(DOWNLOADS_DIR, folder_name))
            if safe_root:
                roots.add(safe_root)
        if not roots:
            cover_path = self._safe_download_path(manga.get('cover_path', ''))
            if cover_path:
                roots.add(os.path.dirname(cover_path))
            for chapter in manga.get('downloaded_chapters', {}).values():
                chapter_path = self._safe_download_path(chapter.get('path', ''))
                if chapter_path:
                    roots.add(chapter_path)
        unique_roots = []
        for root in sorted(roots, key=len):
            if any((os.path.commonpath([root, parent]) == parent for parent in unique_roots)):
                continue
            unique_roots.append(root)
        total = 0
        for root in unique_roots:
            if not os.path.isdir(root):
                continue
            for current, _dirs, files in os.walk(root):
                for filename in files:
                    try:
                        total += os.path.getsize(os.path.join(current, filename))
                    except OSError:
                        continue
        return total

    def is_chapter_downloaded(self, manga_id: str, chapter_id: str) -> bool:
        with db.get_connection() as conn:
            cur = conn.execute('SELECT id FROM downloaded_chapters WHERE manga_id = ? AND id = ?', (manga_id, chapter_id))
            return bool(cur.fetchone())

    @staticmethod
    def _remove_readonly(func, path, _exc_info):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    @staticmethod
    def _managed_path_info(path: str) -> tuple[str, str, str] | None:
        if not path:
            return None
        candidate = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
        candidate = os.path.realpath(candidate)
        for root, url_prefix in ((DOWNLOADS_DIR, '/downloads'), (LOCAL_MANGA_DIR, '/local-manga')):
            managed_root = os.path.realpath(root)
            try:
                if os.path.commonpath([candidate, managed_root]) == managed_root:
                    return (candidate, managed_root, url_prefix)
            except ValueError:
                continue
        return None

    @classmethod
    def _safe_download_path(cls, path: str) -> str | None:
        info = cls._managed_path_info(path)
        return info[0] if info else None

    @classmethod
    def managed_file_url(cls, path: str) -> str:
        info = cls._managed_path_info(path)
        if not info:
            return ''
        candidate, root, prefix = info
        relative = os.path.relpath(candidate, root).replace('\\', '/')
        return f'{prefix}/{relative}'

    def remove_manga(self, manga_id: str) -> bool:
        manga = self.get_manga(manga_id)
        if not manga:
            return False
        manga_dirs = set()
        folder_name = manga.get('folder_name', '')
        if folder_name:
            manga_dirs.add(os.path.join(DOWNLOADS_DIR, folder_name))
        cover_path = self._safe_download_path(manga.get('cover_path', ''))
        if cover_path:
            manga_dirs.add(os.path.dirname(cover_path))
        for chapter in manga.get('downloaded_chapters', {}).values():
            chapter_path = self._safe_download_path(chapter.get('path', ''))
            if chapter_path:
                manga_dirs.add(os.path.dirname(chapter_path))
        downloads_root = os.path.realpath(DOWNLOADS_DIR)
        for manga_dir in manga_dirs:
            safe_dir = self._safe_download_path(manga_dir)
            if not safe_dir or safe_dir == downloads_root:
                continue
            if os.path.exists(safe_dir):
                try:
                    shutil.rmtree(safe_dir, onerror=self._remove_readonly)
                except Exception as e:
                    print(f'Error deleting manga directory {safe_dir}: {e}')
                    return False
        with db.get_connection() as conn:
            self._delete_manga_rows(conn, manga_id)
            conn.commit()
        return True

    @staticmethod
    def _delete_manga_rows(conn: sqlite3.Connection, manga_id: str) -> None:
        """Remove every database-owned relation in the caller's transaction."""
        conn.execute('DELETE FROM downloaded_chapters WHERE manga_id = ?', (manga_id,))
        conn.execute('DELETE FROM manga_source_bindings WHERE manga_id = ?', (manga_id,))
        conn.execute('DELETE FROM mal_outbound_queue WHERE manga_id = ?', (manga_id,))
        conn.execute('DELETE FROM mangas WHERE id = ?', (manga_id,))

    def remove_downloaded_chapter(self, manga_id: str, chapter_id: str) -> bool:
        manga = self.get_manga(manga_id)
        if not manga:
            return False
        chapters = manga.get('downloaded_chapters', {})
        if chapter_id in chapters:
            chapter_rel_path = chapters[chapter_id].get('path', '')
            chapter_abs_path = self._safe_download_path(chapter_rel_path)
            if chapter_abs_path and os.path.exists(chapter_abs_path):
                try:
                    shutil.rmtree(chapter_abs_path, onerror=self._remove_readonly)
                except Exception as e:
                    print(f'Error deleting chapter directory: {e}')
                    return False
            with db.get_connection() as conn:
                conn.execute('DELETE FROM downloaded_chapters WHERE manga_id = ? AND id = ?', (manga_id, chapter_id))
                conn.commit()
            remaining = self.get_manga(manga_id).get('downloaded_chapters', {})
            if not remaining:
                if manga.get('last_read_chapter'):
                    with db.get_connection() as conn:
                        conn.execute("UPDATE mangas SET cover_path = '', folder_name = '' WHERE id = ?", (manga_id,))
                        conn.commit()
                    return True
                return self.remove_manga(manga_id)
            return True
        return False

    def repair_missing_download_records(self):
        changed = False
        with db.get_connection() as conn:
            cur = conn.execute('SELECT * FROM mangas')
            mangas_rows = cur.fetchall()
            for m_row in mangas_rows:
                manga_id = m_row['id']
                c_cur = conn.execute('SELECT * FROM downloaded_chapters WHERE manga_id = ?', (manga_id,))
                c_rows = c_cur.fetchall()
                if not c_rows:
                    continue
                for c_row in c_rows:
                    chapter_id = c_row['id']
                    chapter_path = self._safe_download_path(c_row['path'])
                    if not chapter_path or not os.path.isdir(chapter_path):
                        conn.execute('DELETE FROM downloaded_chapters WHERE id = ? AND manga_id = ?', (chapter_id, manga_id))
                        changed = True
                        print(f"[Library] Kayip bolum kaydi temizlendi: {m_row['title']} / {chapter_id}")
                if changed:
                    c_cur = conn.execute('SELECT * FROM downloaded_chapters WHERE manga_id = ?', (manga_id,))
                    c_rows_after = c_cur.fetchall()
                    if not c_rows_after:
                        if m_row['last_read_chapter']:
                            conn.execute("UPDATE mangas SET cover_path = '', folder_name = '' WHERE id = ?", (manga_id,))
                        else:
                            self._delete_manga_rows(conn, manga_id)
                            changed = True
            if changed:
                conn.commit()

    def repair_missing_metadata(self):
        changed = False
        with db.get_connection() as conn:
            cur = conn.execute('SELECT * FROM mangas')
            for manga in cur.fetchall():
                manga_id = manga['id']
                manga_dict = dict(manga)
                if manga_id.startswith('anilist_'):
                    if not manga_dict.get('cover_url') or not manga_dict.get('description') or manga_dict.get('status') == 'unknown':
                        try:
                            raise RuntimeError('Reader çevrimiçi servis içermez.')
                            search_title = manga_dict.get('title', '')
                            if not search_title or search_title == 'Unknown Manga':
                                search_title = manga_dict.get('folder_name', '')
                            if search_title:
                                meta = get_anilist_metadata(search_title)
                                if meta:
                                    updates = []
                                    params = []
                                    if not manga_dict.get('title') or manga_dict['title'] == 'Unknown Manga':
                                        updates.append('title = ?')
                                        params.append(meta['title'])
                                    updates.append('description = ?')
                                    params.append(meta['description'])
                                    updates.append('cover_url = ?')
                                    params.append(meta['cover_url'])
                                    updates.append('status = ?')
                                    params.append(meta['status'])
                                    if meta.get('tags'):
                                        updates.append('tags = ?')
                                        params.append(json.dumps(meta['tags']))
                                    if meta.get('year'):
                                        updates.append('year = ?')
                                        params.append(meta['year'])
                                    if not manga_dict.get('cover_path'):
                                        try:
                                            import httpx
                                            from PIL import Image
                                            import io
                                            from mangax.core.migrate_folders import _safe_folder_name, _set_folder_icon
                                            folder_name = manga_dict.get('folder_name') or manga_dict.get('title')
                                            safe_name = _safe_folder_name(folder_name)
                                            manga_dir = os.path.join(DOWNLOADS_DIR, safe_name)
                                            os.makedirs(manga_dir, exist_ok=True)
                                            cover_path = os.path.join(manga_dir, 'cover.webp')
                                            resp = httpx.get(meta['cover_url'], timeout=10.0)
                                            if resp.status_code == 200:
                                                img = Image.open(io.BytesIO(resp.content)).convert('RGB')
                                                img.save(cover_path, 'WEBP', quality=75)
                                                rel_path = os.path.relpath(cover_path, BASE_DIR).replace('\\', '/')
                                                updates.append('cover_path = ?')
                                                params.append(rel_path)
                                                _set_folder_icon(manga_dir, cover_path)
                                        except Exception as dl_err:
                                            print(f'Error downloading cover for repaired manga {manga_id}: {dl_err}')
                                    params.append(manga_id)
                                    conn.execute(f"UPDATE mangas SET {', '.join(updates)} WHERE id = ?", params)
                                    changed = True
                        except Exception as e:
                            print(f'Error repairing metadata for {manga_id}: {e}')
            if changed:
                conn.commit()

    def repair_missing_covers(self):
        changed = False
        with db.get_connection() as conn:
            cur = conn.execute('SELECT * FROM mangas')
            for manga in cur.fetchall():
                manga_id = manga['id']
                manga_dict = dict(manga)
                c_cur = conn.execute('SELECT id FROM downloaded_chapters WHERE manga_id = ?', (manga_id,))
                if not c_cur.fetchone():
                    continue
                cover_rel_path = manga_dict.get('cover_path', '')
                cover_abs_path = os.path.join(BASE_DIR, cover_rel_path) if cover_rel_path else ''
                if cover_abs_path and os.path.isfile(cover_abs_path):
                    continue
                cover_url = manga_dict.get('cover_url', '')
                if not cover_url or not cover_url.startswith(('http://', 'https://')):
                    continue
                try:
                    import io
                    import httpx
                    from PIL import Image
                    from mangax.core.migrate_folders import _safe_folder_name
                    folder_name = manga_dict.get('folder_name') or manga_dict.get('title') or manga_id
                    safe_name = _safe_folder_name(folder_name)
                    manga_dir = os.path.join(DOWNLOADS_DIR, safe_name)
                    os.makedirs(manga_dir, exist_ok=True)
                    repaired_cover_path = os.path.join(manga_dir, 'cover.webp')
                    request_options = {'headers': {'User-Agent': 'Mozilla/5.0'}, 'timeout': 15.0, 'follow_redirects': True}
                    response = httpx.get(cover_url, **request_options)
                    if response.status_code >= 400:
                        raise RuntimeError('Reader çevrimiçi servis içermez.')
                        metadata = get_anilist_metadata(manga_dict.get('title', ''))
                        fallback_url = metadata.get('cover_url', '') if metadata else ''
                        if not fallback_url:
                            response.raise_for_status()
                        cover_url = fallback_url
                        conn.execute('UPDATE mangas SET cover_url = ? WHERE id = ?', (fallback_url, manga_id))
                        response = httpx.get(fallback_url, **request_options)
                    response.raise_for_status()
                    image = Image.open(io.BytesIO(response.content)).convert('RGB')
                    image.save(repaired_cover_path, 'WEBP', quality=82)
                    rel_path = os.path.relpath(repaired_cover_path, BASE_DIR).replace('\\', '/')
                    conn.execute('UPDATE mangas SET cover_path = ?, folder_name = ? WHERE id = ?', (rel_path, safe_name, manga_id))
                    changed = True
                    print(f"[Library] Eksik kapak onarildi: {manga_dict.get('title', manga_id)}")
                except Exception as e:
                    print(f'Error repairing cover for {manga_id}: {e}')
            if changed:
                conn.commit()
if __name__ == '__main__':
    lm = LibraryManager()
    print('Library initialized with SQLite backend.')
