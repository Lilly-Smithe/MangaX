"""MyAnimeList listesini MangaX kütüphanesiyle idempotent eşitleyen servis."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from mangax.core.database import db
from mangax.core.dependencies import library_manager
from mangax.core.external_identity import (
    ExternalIdentityService,
    IdentityConflictError,
)
from mangax.core.library import LibraryManager
from mangax.integrations.mal_integration import (
    MalIntegrationError,
    MalIntegrationManager,
    mal_integration_manager,
)
from mangax.integrations.mal_outbound import _dumps, _loads, _payload


MAL_LIBRARY_STATUS = {
    "reading": "reading",
    "completed": "completed",
    "on_hold": "on_hold",
    "dropped": "dropped",
    "plan_to_read": "plan_to_read",
}
MAL_ID_PREFIXES = ("anilist_", "mal_")


class MalSyncCancelled(RuntimeError):
    """İptal edilen senkronun açık SQLite transaction'ını rollback ettirir."""


class MalSyncService:
    """MAL kaynaklı alanları tek SQLite transaction içinde günceller."""

    def __init__(
        self,
        integration_manager: MalIntegrationManager,
        library_manager: LibraryManager,
        database_manager=db,
    ):
        self.integration_manager = integration_manager
        self.library_manager = library_manager
        self.database = database_manager
        self.identities = ExternalIdentityService(database_manager)
        self._sync_lock = threading.Lock()

    @staticmethod
    def _nonnegative_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return list(value)
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    def _collections(self, existing: Any, mal_status: str) -> str:
        collections = self._json_list(existing)
        collections.append("MyAnimeList")
        if mal_status == "plan_to_read":
            collections.append("MAL: Okuma Planı")
        return json.dumps(
            self.library_manager._clean_collections(collections),
            ensure_ascii=False,
        )

    def _managed_values(
        self,
        entry: dict[str, Any],
        manga: dict[str, Any],
        existing: dict[str, Any] | None,
    ) -> dict[str, Any]:
        mal_status = str(entry.get("status") or "").strip()
        if mal_status not in MAL_LIBRARY_STATUS:
            raise ValueError("Desteklenmeyen MAL okuma durumu")

        existing = existing or {}
        tags = manga.get("tags")
        if not isinstance(tags, list) or not tags:
            tags = self._json_list(existing.get("tags"))
        year = self._nonnegative_int(manga.get("year"))
        if not year:
            year = self._nonnegative_int(existing.get("year"))
        external_titles = manga.get("_external_titles") or manga.get("_search_titles") or []
        if not isinstance(external_titles, list) or not external_titles:
            external_titles = self._json_list(existing.get("external_titles"))
        normalized_titles = []
        for value in external_titles:
            title = str(value or "").strip()
            if title and title not in normalized_titles:
                normalized_titles.append(title[:300])
        manga_id = str(manga.get("id") or "")
        anilist_id = self._nonnegative_int(manga.get("anilist_id"))
        if not anilist_id and manga_id.startswith("anilist_"):
            anilist_id = self._nonnegative_int(manga_id.removeprefix("anilist_"))

        return {
            "title": str(
                manga.get("title")
                or entry.get("title")
                or existing.get("title")
                or "Bilinmeyen Manga"
            ),
            "description": str(manga.get("description") or existing.get("description") or ""),
            "cover_url": str(
                entry.get("cover_url")
                or manga.get("cover_url")
                or existing.get("cover_url")
                or ""
            ),
            "status": str(manga.get("status") or existing.get("status") or "unknown"),
            "tags": json.dumps(tags, ensure_ascii=False),
            "year": year,
            "library_status": MAL_LIBRARY_STATUS[mal_status],
            "user_rating": min(10, self._nonnegative_int(entry.get("score"))),
            "collections": self._collections(existing.get("collections"), mal_status),
            "mal_id": self._nonnegative_int(entry.get("mal_id")),
            "mal_status": mal_status,
            "mal_num_chapters_read": self._nonnegative_int(entry.get("num_chapters_read")),
            "mal_num_volumes_read": self._nonnegative_int(entry.get("num_volumes_read")),
            "mal_remote_score": min(10, self._nonnegative_int(entry.get("score"))),
            "mal_remote_updated_at": str(entry.get("remote_updated_at") or "")[:80],
            "mal_sync_error": "",
            "anilist_id": anilist_id or self._nonnegative_int(existing.get("anilist_id")),
            "external_titles": json.dumps(normalized_titles[:50], ensure_ascii=False),
        }

    @staticmethod
    def _is_changed(existing: dict[str, Any], values: dict[str, Any]) -> bool:
        for key, value in values.items():
            current = existing.get(key)
            if key in {
                "mal_id", "mal_num_chapters_read", "mal_num_volumes_read",
                "mal_remote_score", "user_rating", "year",
                "anilist_id",
            }:
                try:
                    current = int(current or 0)
                except (TypeError, ValueError):
                    current = 0
            if current != value:
                return True
        return False

    @staticmethod
    def _record_error(
        conn,
        existing: dict[str, Any] | None,
        message: str,
        synced_at: int,
    ) -> None:
        if not existing:
            return
        conn.execute(
            """
            UPDATE mangas
            SET mal_last_synced_at = ?, mal_sync_error = ?
            WHERE id = ?
            """,
            (synced_at, str(message or "")[:500], existing["id"]),
        )

    def sync(
        self,
        *,
        progress_callback: Callable[[str, int, int, dict[str, int]], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Aynı süreçte birden fazla senkronun çakışmasını önler."""
        with self._sync_lock:
            return self._sync_once(
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )

    def _sync_once(
        self,
        *,
        progress_callback: Callable[[str, int, int, dict[str, int]], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Uzak MAL listesinin tamamını ekler/günceller; uzakta olmayanı silmez."""
        def progress(stage: str, processed: int, total: int, counts: dict[str, int] | None = None) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise MalSyncCancelled("MyAnimeList senkronizasyonu iptal edildi.")
            if progress_callback:
                progress_callback(stage, processed, total, dict(counts or {}))

        preview = self.integration_manager.fetch_preview(
            force=True,
            progress_callback=lambda stage, processed, total: progress(
                stage, processed, total,
            ),
            cancel_event=cancel_event,
        )
        entries = preview.get("entries") if isinstance(preview, dict) else []
        if not isinstance(entries, list):
            raise ValueError("MyAnimeList eşitleme yanıtı geçersiz")

        result: dict[str, Any] = {
            "added": 0,
            "updated": 0,
            "unchanged": 0,
            "unmatched": 0,
            "failed": 0,
            "conflicts": 0,
            "errors": [],
        }
        synced_at = int(time.time())
        seen_mal_ids: set[int] = set()
        try:
            account_key = str(self.integration_manager.account_identity() or "")
        except (AttributeError, MalIntegrationError):
            account_key = ""
        progress("importing", 0, len(entries), result)

        with self.database.get_connection() as conn:
            # Kayıt savepoint'leri her koşulda tek toplu transaction'ın içinde
            # kalsın; iptal veya kapanma bütün kısmi içe aktarımı geri alabilsin.
            conn.execute("BEGIN IMMEDIATE")
            rows = [dict(row) for row in conn.execute("SELECT * FROM mangas").fetchall()]
            by_id = {str(row["id"]): row for row in rows}
            by_mal_id: dict[int, list[dict[str, Any]]] = {}
            for row in rows:
                legacy_mal_id = self._nonnegative_int(row.get("mal_id"))
                if legacy_mal_id > 0:
                    by_mal_id.setdefault(legacy_mal_id, []).append(row)

            for index, raw_entry in enumerate(entries, start=1):
                progress("importing", index - 1, len(entries), result)
                entry = raw_entry if isinstance(raw_entry, dict) else {}
                mal_id = self._nonnegative_int(entry.get("mal_id"))
                existing = None
                savepoint = f"mal_sync_entry_{index}"
                try:
                    if not mal_id:
                        raise ValueError("Geçersiz MAL kimliği")
                    if mal_id in seen_mal_ids:
                        raise ValueError("MAL listesinde yinelenen kimlik")
                    seen_mal_ids.add(mal_id)

                    manga = entry.get("manga") if isinstance(entry.get("manga"), dict) else None
                    incoming_anilist_id = self._nonnegative_int(
                        (manga or {}).get("anilist_id")
                    )
                    incoming_manga_id = str((manga or {}).get("id") or "")
                    if not incoming_anilist_id and incoming_manga_id.startswith("anilist_"):
                        incoming_anilist_id = self._nonnegative_int(
                            incoming_manga_id.removeprefix("anilist_")
                        )
                    exact_pair = bool(entry.get("matched") and incoming_anilist_id)
                    resolution = self.identities.resolve(
                        mal_id=mal_id,
                        anilist_id=incoming_anilist_id,
                        exact_pair=exact_pair,
                        conn=conn,
                    )
                    if resolution.conflict:
                        raise IdentityConflictError(
                            "MAL kimliği birden fazla MangaX kaydıyla çakışıyor.",
                            conflict_key=resolution.conflict_key,
                        )
                    if resolution.manga_id:
                        existing = by_id.get(resolution.manga_id)
                    legacy_matches = by_mal_id.get(mal_id, [])
                    if existing is None and len(legacy_matches) > 1:
                        conflict_key = f"duplicate:myanimelist:{mal_id}"
                        self.identities.record_conflict(
                            conflict_key=conflict_key,
                            conflict_type="duplicate_external_id",
                            provider="myanimelist",
                            external_id=mal_id,
                            manga_ids=[str(item["id"]) for item in legacy_matches],
                            conn=conn,
                        )
                        raise IdentityConflictError(
                            "Aynı MAL kimliği birden fazla MangaX kaydında bulunuyor.",
                            conflict_key=conflict_key,
                        )
                    if existing is None and len(legacy_matches) == 1:
                        existing = legacy_matches[0]
                    if existing is None and manga is None:
                        result["unmatched"] += 1
                        continue
                    manga = manga or {
                        "id": existing["id"],
                        "title": entry.get("title"),
                        "cover_url": entry.get("cover_url"),
                    }
                    target_id = str(existing["id"] if existing else manga.get("id") or "").strip()
                    if not target_id.startswith(MAL_ID_PREFIXES):
                        raise ValueError("MAL kaydı güvenilir bir manga kimliğiyle eşleşmedi")

                    if existing is None:
                        existing = by_id.get(target_id)
                        existing_mal_id = self._nonnegative_int(
                            existing.get("mal_id") if existing else 0
                        )
                        if existing_mal_id not in {0, mal_id}:
                            raise ValueError("Manga kimliği başka bir MAL kaydıyla eşleşmiş")

                    values = self._managed_values(entry, manga, existing)
                    conn.execute(f"SAVEPOINT {savepoint}")
                    if existing is None:
                        conn.execute(
                            """
                            INSERT INTO mangas (
                                id, title, description, cover_url, cover_path, status,
                                folder_name, tags, year, last_read_chapter, last_read_page,
                                last_read_chapter_num, last_read_chapter_title,
                                last_read_source_id, last_read_language, last_read_online,
                                last_read_at, library_status, user_rating, personal_note,
                                collections, updated_at, mal_id, mal_status,
                                mal_num_chapters_read, mal_num_volumes_read,
                                mal_remote_score, mal_last_synced_at,
                                mal_remote_updated_at, mal_sync_error,
                                anilist_id, external_titles
                            ) VALUES (
                                ?, ?, ?, ?, '', ?, '', ?, ?, '', 0, '', '', '', 'tr', ?,
                                0, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, '',
                                ?, ?
                            )
                            """,
                            (
                                target_id,
                                values["title"],
                                values["description"],
                                values["cover_url"],
                                values["status"],
                                values["tags"],
                                values["year"],
                                0 if target_id.startswith("mal_") else 1,
                                values["library_status"],
                                values["user_rating"],
                                values["collections"],
                                synced_at,
                                values["mal_id"],
                                values["mal_status"],
                                values["mal_num_chapters_read"],
                                values["mal_num_volumes_read"],
                                values["mal_remote_score"],
                                synced_at,
                                values["mal_remote_updated_at"],
                                values["anilist_id"],
                                values["external_titles"],
                            ),
                        )
                        if exact_pair:
                            self.identities.link_exact_pair(
                                target_id,
                                mal_id=mal_id,
                                anilist_id=incoming_anilist_id,
                                match_method="anilist_idmal",
                                conn=conn,
                            )
                        else:
                            self.identities.link(
                                target_id,
                                "myanimelist",
                                mal_id,
                                confidence="exact",
                                match_method="mal_sync",
                                verified=True,
                                conn=conn,
                            )
                        inserted = dict(values)
                        inserted["id"] = target_id
                        by_id[target_id] = inserted
                        by_mal_id.setdefault(mal_id, []).append(inserted)
                        result["added"] += 1
                        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                        progress("importing", index, len(entries), result)
                        continue

                    queued = (
                        conn.execute(
                            """
                            SELECT * FROM mal_outbound_queue
                            WHERE manga_id = ? AND account_key = ?
                            """,
                            (existing["id"], account_key),
                        ).fetchone()
                        if account_key else None
                    )
                    if queued:
                        remote_payload = _payload({
                            "status": values["mal_status"],
                            "score": values["mal_remote_score"],
                            "num_chapters_read": values["mal_num_chapters_read"],
                            "num_volumes_read": values["mal_num_volumes_read"],
                        })
                        base_payload = _loads(queued["base_payload"])
                        desired_payload = _loads(queued["desired_payload"])
                        if remote_payload == desired_payload:
                            conn.execute(
                                "DELETE FROM mal_outbound_queue WHERE manga_id = ?",
                                (existing["id"],),
                            )
                        else:
                            # Kuyruktaki yerel tercih uzaktaki temel değer üzerine kuruludur.
                            # Uzak da değiştiyse yerel alanları sessizce ezme.
                            values["library_status"] = existing.get("library_status") or "reading"
                            values["user_rating"] = self._nonnegative_int(existing.get("user_rating"))
                            if remote_payload != base_payload:
                                conn.execute(
                                    """
                                    UPDATE mal_outbound_queue
                                    SET state = 'conflict', remote_payload = ?,
                                        last_error = '', updated_at = ?
                                    WHERE manga_id = ?
                                    """,
                                    (_dumps(remote_payload), synced_at, existing["id"]),
                                )
                                conn.execute(
                                    """
                                    INSERT OR IGNORE INTO chapter_notifications (
                                        type, title, message, manga_id,
                                        created_at, read, dedupe_key
                                    ) VALUES (
                                        'mal_conflict', 'MyAnimeList çakışması',
                                        ?, ?, ?, 0, ?
                                    )
                                    """,
                                    (
                                        f"{existing.get('title') or 'Manga'} hem MangaX'te hem MyAnimeList'te değişti. Entegrasyonlar ekranından korunacak sürümü seçin.",
                                        existing["id"],
                                        synced_at,
                                        f"mal_conflict:{account_key}:{existing['id']}",
                                    ),
                                )
                                result["conflicts"] += 1

                    changed = self._is_changed(existing, values)
                    conn.execute(
                        """
                        UPDATE mangas SET
                            title = ?, description = ?, cover_url = ?, status = ?,
                            tags = ?, year = ?, library_status = ?, user_rating = ?,
                            collections = ?, mal_id = ?, mal_status = ?,
                            mal_num_chapters_read = ?, mal_num_volumes_read = ?,
                            mal_remote_score = ?,
                            mal_last_synced_at = ?, mal_remote_updated_at = ?,
                            mal_sync_error = ?, anilist_id = ?, external_titles = ?,
                            updated_at = CASE WHEN ? THEN ? ELSE updated_at END
                        WHERE id = ?
                        """,
                        (
                            values["title"],
                            values["description"],
                            values["cover_url"],
                            values["status"],
                            values["tags"],
                            values["year"],
                            values["library_status"],
                            values["user_rating"],
                            values["collections"],
                            values["mal_id"],
                            values["mal_status"],
                            values["mal_num_chapters_read"],
                            values["mal_num_volumes_read"],
                            values["mal_remote_score"],
                            synced_at,
                            values["mal_remote_updated_at"],
                            values["mal_sync_error"],
                            values["anilist_id"],
                            values["external_titles"],
                            changed,
                            synced_at,
                            existing["id"],
                        ),
                    )
                    if exact_pair:
                        self.identities.link_exact_pair(
                            existing["id"],
                            mal_id=mal_id,
                            anilist_id=incoming_anilist_id,
                            match_method="anilist_idmal",
                            conn=conn,
                        )
                    else:
                        self.identities.link(
                            existing["id"],
                            "myanimelist",
                            mal_id,
                            confidence="exact",
                            match_method="mal_sync",
                            verified=True,
                            conn=conn,
                        )
                    existing.update(values)
                    existing["mal_last_synced_at"] = synced_at
                    if all(item["id"] != existing["id"] for item in by_mal_id.setdefault(mal_id, [])):
                        by_mal_id[mal_id].append(existing)
                    result["updated" if changed else "unchanged"] += 1
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                except (IdentityConflictError, KeyError, TypeError, ValueError, OverflowError) as error:
                    try:
                        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                    except Exception:
                        pass
                    if isinstance(error, IdentityConflictError):
                        result["conflicts"] += 1
                    message = str(error) or "MAL kaydı eşitlenemedi"
                    self._record_error(conn, existing, message, synced_at)
                    result["failed"] += 1
                    result["errors"].append({
                        "mal_id": mal_id,
                        "title": str(entry.get("title") or ""),
                        "error": message,
                    })
                progress("importing", index, len(entries), result)
            conn.commit()

        result["total"] = len(entries)
        result["synced_at"] = synced_at
        result["read_only"] = True
        return result


mal_sync_service = MalSyncService(
    integration_manager=mal_integration_manager,
    library_manager=library_manager,
)
