"""MangaX ortak veritabanı katmanı."""

import sqlite3
import json
import os
import time
from contextlib import contextmanager
from mangax.core.config import DATA_DIR
from mangax.core.models import LIBRARY_STATUS_VALUES
from typing import Dict, Any, List, Optional

DB_PATH = os.path.join(DATA_DIR, "library.db")
DATABASE_SCHEMA_VERSION = 7
SQLITE_BUSY_TIMEOUT_MS = 5000
LIBRARY_STATUS_SQL_VALUES = ", ".join(
    f"'{value}'" for value in sorted(LIBRARY_STATUS_VALUES)
)

class DatabaseManager:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(
            DB_PATH,
            check_same_thread=False,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self):
        with self.get_connection() as conn:
            # WAL is persistent for the database file. Set it during schema
            # initialization instead of renegotiating it on every connection.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS mangas (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    description TEXT,
                    cover_url TEXT,
                    cover_path TEXT,
                    status TEXT,
                    folder_name TEXT,
                    tags TEXT,
                    year INTEGER,
                    last_read_chapter TEXT,
                    last_read_page INTEGER,
                    last_read_chapter_num TEXT,
                    last_read_chapter_title TEXT,
                    last_read_source_id TEXT,
                    last_read_language TEXT,
                    last_read_online BOOLEAN,
                    last_read_at INTEGER,
                    last_read_offset REAL DEFAULT 0,
                    last_read_percent REAL DEFAULT 0,
                    library_status TEXT NOT NULL DEFAULT 'reading'
                        CHECK (library_status IN ({LIBRARY_STATUS_SQL_VALUES})),
                    user_rating INTEGER DEFAULT 0,
                    personal_note TEXT DEFAULT '',
                    collections TEXT DEFAULT '[]',
                    known_chapters TEXT DEFAULT '[]',
                    unread_count INTEGER DEFAULT 0,
                    updated_at INTEGER DEFAULT 0,
                    tracking_enabled INTEGER DEFAULT 0,
                    tracking_notifications INTEGER DEFAULT 1,
                    tracking_auto_download INTEGER DEFAULT 0,
                    tracking_source_manga_id TEXT DEFAULT '',
                    tracking_source_name TEXT DEFAULT '',
                    tracking_last_checked_at INTEGER DEFAULT 0,
                    tracking_last_error TEXT DEFAULT '',
                    mal_id INTEGER DEFAULT 0,
                    mal_status TEXT DEFAULT '',
                    mal_num_chapters_read INTEGER DEFAULT 0,
                    mal_num_volumes_read INTEGER DEFAULT 0,
                    mal_remote_score INTEGER DEFAULT 0,
                    mal_last_synced_at INTEGER DEFAULT 0,
                    mal_remote_updated_at TEXT DEFAULT '',
                    mal_sync_error TEXT DEFAULT '',
                    anilist_id INTEGER DEFAULT 0,
                    external_titles TEXT DEFAULT '[]'
                )
            """)
            existing_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(mangas)").fetchall()
            }
            if "last_read_offset" not in existing_columns:
                conn.execute("ALTER TABLE mangas ADD COLUMN last_read_offset REAL DEFAULT 0")
            if "last_read_percent" not in existing_columns:
                conn.execute("ALTER TABLE mangas ADD COLUMN last_read_percent REAL DEFAULT 0")
            library_columns = {
                "library_status": "TEXT DEFAULT 'reading'",
                "user_rating": "INTEGER DEFAULT 0",
                "personal_note": "TEXT DEFAULT ''",
                "collections": "TEXT DEFAULT '[]'",
                "known_chapters": "TEXT DEFAULT '[]'",
                "unread_count": "INTEGER DEFAULT 0",
                "updated_at": "INTEGER DEFAULT 0",
                "tracking_enabled": "INTEGER DEFAULT 0",
                "tracking_notifications": "INTEGER DEFAULT 1",
                "tracking_auto_download": "INTEGER DEFAULT 0",
                "tracking_source_manga_id": "TEXT DEFAULT ''",
                "tracking_source_name": "TEXT DEFAULT ''",
                "tracking_last_checked_at": "INTEGER DEFAULT 0",
                "tracking_last_error": "TEXT DEFAULT ''",
                "mal_id": "INTEGER DEFAULT 0",
                "mal_status": "TEXT DEFAULT ''",
                "mal_num_chapters_read": "INTEGER DEFAULT 0",
                "mal_num_volumes_read": "INTEGER DEFAULT 0",
                "mal_remote_score": "INTEGER DEFAULT 0",
                "mal_last_synced_at": "INTEGER DEFAULT 0",
                "mal_remote_updated_at": "TEXT DEFAULT ''",
                "mal_sync_error": "TEXT DEFAULT ''",
                "anilist_id": "INTEGER DEFAULT 0",
                "external_titles": "TEXT DEFAULT '[]'",
            }
            for column, definition in library_columns.items():
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE mangas ADD COLUMN {column} {definition}")
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema_version < 1:
                # v0.12.4 ve öncesinde MAL plan_to_read kayıtları zorunlu olarak
                # on_hold saklanıyordu. Yalnız MAL kökeni kesin olan satırları
                # düzelt; kullanıcının elle seçtiği mevcut durumlara dokunma.
                conn.execute("""
                    UPDATE mangas
                    SET library_status = 'plan_to_read'
                    WHERE mal_status = 'plan_to_read'
                      AND library_status = 'on_hold'
                """)
                conn.execute("PRAGMA user_version = 1")
                schema_version = 1
            if schema_version < 2:
                conn.execute("PRAGMA user_version = 2")
                schema_version = 2
            if schema_version < 3:
                # Eski kayıtlarda yerel puan son MAL içe aktarımından geliyordu.
                # Uzak temel değeri ayrı saklayarak sonraki yerel değişiklikleri
                # güvenli biçimde çakışma denetimine sok.
                conn.execute("""
                    UPDATE mangas
                    SET mal_remote_score = user_rating
                    WHERE mal_id > 0
                """)
                conn.execute("PRAGMA user_version = 3")
                schema_version = 3
            if schema_version < 4:
                # Eski anilist_<id> kayıtlarının dış kimliği anahtar değiştirmeden
                # doldurulur. mal_<id> kimlikleri daha sonra Full eşleştiricisi
                # tarafından güvenli biçimde zenginleştirilebilir.
                conn.execute("""
                    UPDATE mangas
                    SET anilist_id = CAST(SUBSTR(id, 9) AS INTEGER)
                    WHERE id GLOB 'anilist_[0-9]*'
                      AND COALESCE(anilist_id, 0) = 0
                """)
                conn.execute("PRAGMA user_version = 4")
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mangas_mal_id
                ON mangas(mal_id) WHERE mal_id > 0
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mangas_anilist_id
                ON mangas(anilist_id) WHERE anilist_id > 0
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS manga_external_identities (
                    manga_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    confidence TEXT NOT NULL DEFAULT 'exact'
                        CHECK (confidence IN ('exact', 'high', 'low', 'manual')),
                    match_method TEXT NOT NULL DEFAULT '',
                    verified_at INTEGER NOT NULL DEFAULT 0,
                    last_checked_at INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (manga_id, provider, external_id),
                    FOREIGN KEY(manga_id) REFERENCES mangas(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_external_identity_lookup
                ON manga_external_identities(provider, external_id, confidence)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_external_identity_manga
                ON manga_external_identities(manga_id, provider)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS manga_identity_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conflict_key TEXT NOT NULL UNIQUE,
                    conflict_type TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    external_id TEXT NOT NULL DEFAULT '',
                    manga_ids TEXT NOT NULL DEFAULT '[]',
                    details TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'resolved', 'ignored')),
                    created_at INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS manga_source_bindings (
                    manga_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_manga_id TEXT NOT NULL,
                    source_title TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'ambiguous', 'broken', 'unavailable')),
                    manual INTEGER NOT NULL DEFAULT 0,
                    chapter_count INTEGER NOT NULL DEFAULT 0,
                    verified_at INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (manga_id, source_id, source_manga_id),
                    FOREIGN KEY(manga_id) REFERENCES mangas(id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source_bindings_manga
                ON manga_source_bindings(manga_id, status, confidence DESC)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS manga_source_resolution_cache (
                    manga_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    title_signature TEXT NOT NULL DEFAULT '',
                    plugin_fingerprint TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    searched_at INTEGER NOT NULL DEFAULT 0,
                    expires_at INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(manga_id) REFERENCES mangas(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source_resolution_cache_expiry
                ON manga_source_resolution_cache(expires_at)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS downloaded_chapters (
                    id TEXT,
                    manga_id TEXT,
                    chapter TEXT,
                    title TEXT,
                    language TEXT,
                    pages TEXT,
                    path TEXT,
                    PRIMARY KEY (id, manga_id),
                    FOREIGN KEY(manga_id) REFERENCES mangas(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chapter_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    manga_id TEXT DEFAULT '',
                    created_at INTEGER NOT NULL,
                    read INTEGER DEFAULT 0,
                    dedupe_key TEXT UNIQUE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mal_outbound_queue (
                    manga_id TEXT PRIMARY KEY,
                    mal_id INTEGER NOT NULL,
                    account_key TEXT NOT NULL DEFAULT '',
                    base_payload TEXT NOT NULL DEFAULT '{}',
                    desired_payload TEXT NOT NULL DEFAULT '{}',
                    remote_payload TEXT NOT NULL DEFAULT '{}',
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK (state IN ('pending', 'conflict')),
                    queued_at INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mal_outbound_due
                ON mal_outbound_queue(account_key, state, available_at)
            """)
            if schema_version < 5:
                # Older builds did not consistently enforce foreign keys, so
                # interrupted deletes could leave relation rows behind. Only
                # rows whose parent is certainly absent are removed.
                for table in (
                    "downloaded_chapters",
                    "manga_source_bindings",
                    "mal_outbound_queue",
                ):
                    conn.execute(
                        f"DELETE FROM {table} "
                        "WHERE NOT EXISTS ("
                        f"SELECT 1 FROM mangas WHERE mangas.id = {table}.manga_id"
                        ")"
                    )
                conn.execute("PRAGMA user_version = 5")
                schema_version = 5
            if schema_version < 6:
                # Kimlik kolonları geriye uyumluluk için kalır; yeni tablo ana
                # manga anahtarlarını değiştirmeden sağlayıcı kimliklerini indeksler.
                conn.execute("""
                    UPDATE mangas
                    SET mal_id = CAST(SUBSTR(id, 5) AS INTEGER)
                    WHERE id GLOB 'mal_[0-9]*'
                      AND COALESCE(mal_id, 0) = 0
                """)
                conn.execute("PRAGMA user_version = 6")
                schema_version = 6
            if schema_version < 7:
                # Kaynak bulunamayan katalog kayıtları kısa süreli saklanır. Eklenti
                # parmak izi değiştiğinde servis bu kaydı kullanmayacağından eski bir
                # başarısız sonuç yeni/yenilenmiş eklentileri engellemez.
                conn.execute("PRAGMA user_version = 7")

            # Her başlangıçta idempotent backfill yapılması, farklı sürümlerin aynı
            # DATA_DIR'ı kullanması halinde sonradan eklenen eski kayıtları da kapsar.
            identity_now = int(time.time())
            conn.execute(
                """
                INSERT OR IGNORE INTO manga_external_identities (
                    manga_id, provider, external_id, confidence, match_method,
                    verified_at, last_checked_at, created_at, updated_at
                )
                SELECT id, 'myanimelist', CAST(mal_id AS TEXT), 'exact',
                       'legacy_column', ?, ?, ?, ?
                FROM mangas WHERE COALESCE(mal_id, 0) > 0
                """,
                (identity_now, identity_now, identity_now, identity_now),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO manga_external_identities (
                    manga_id, provider, external_id, confidence, match_method,
                    verified_at, last_checked_at, created_at, updated_at
                )
                SELECT id, 'anilist', CAST(anilist_id AS TEXT), 'exact',
                       'legacy_column', ?, ?, ?, ?
                FROM mangas WHERE COALESCE(anilist_id, 0) > 0
                """,
                (identity_now, identity_now, identity_now, identity_now),
            )
            duplicate_rows = conn.execute(
                """
                SELECT provider, external_id
                FROM manga_external_identities
                GROUP BY provider, external_id
                HAVING COUNT(DISTINCT manga_id) > 1
                """
            ).fetchall()
            for duplicate in duplicate_rows:
                provider = str(duplicate["provider"])
                external_id = str(duplicate["external_id"])
                manga_ids = sorted({
                    str(row[0]) for row in conn.execute(
                        """
                        SELECT manga_id FROM manga_external_identities
                        WHERE provider = ? AND external_id = ?
                        """,
                        (provider, external_id),
                    ).fetchall()
                })
                conflict_type = (
                    "anilist_multiple_mal_records"
                    if provider == "anilist"
                    else "duplicate_external_id"
                )
                conn.execute(
                    """
                    INSERT INTO manga_identity_conflicts (
                        conflict_key, conflict_type, provider, external_id,
                        manga_ids, details, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, '{}', 'open', ?, ?)
                    ON CONFLICT(conflict_key) DO UPDATE SET
                        conflict_type = excluded.conflict_type,
                        manga_ids = excluded.manga_ids,
                        status = 'open',
                        updated_at = excluded.updated_at
                    """,
                    (
                        f"duplicate:{provider}:{external_id}",
                        conflict_type,
                        provider,
                        external_id,
                        json.dumps(manga_ids, ensure_ascii=False),
                        identity_now,
                        identity_now,
                    ),
                )
            conn.commit()

db = DatabaseManager()
