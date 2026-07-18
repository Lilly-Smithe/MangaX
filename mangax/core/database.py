"""MangaX ortak veritabanı katmanı."""

import sqlite3
import json
import os
import time
from contextlib import contextmanager
from mangax.core.config import DATA_DIR
from typing import Dict, Any, List, Optional

DB_PATH = os.path.join(DATA_DIR, "library.db")

class DatabaseManager:
    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self):
        with self.get_connection() as conn:
            conn.execute("""
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
                    library_status TEXT DEFAULT 'reading',
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
                    mal_num_volumes_read INTEGER DEFAULT 0
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
            }
            for column, definition in library_columns.items():
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE mangas ADD COLUMN {column} {definition}")
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
            conn.commit()

db = DatabaseManager()
