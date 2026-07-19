"""MangaX değişikliklerini güvenli, kalıcı bir kuyrukla MyAnimeList'e gönderir."""

from __future__ import annotations

import json
import math
import threading
import time
from typing import Any

from mangax.core.database import db
from mangax.integrations.mal_integration import (
    MalIntegrationError,
    MalIntegrationManager,
    MalRateLimitError,
    mal_integration_manager,
)


MAL_STATUS_VALUES = {"reading", "completed", "on_hold", "dropped", "plan_to_read"}
OUTBOUND_FIELDS = ("status", "score", "num_chapters_read", "num_volumes_read")


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _chapter_int(value: Any) -> int:
    try:
        return max(0, int(math.floor(float(str(value or "0").replace(",", ".")))))
    except (TypeError, ValueError, OverflowError):
        return 0


def _payload(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value or {}
    status = str(source.get("status") or "plan_to_read")
    return {
        "status": status if status in MAL_STATUS_VALUES else "plan_to_read",
        "score": min(10, _nonnegative_int(source.get("score"))),
        "num_chapters_read": _nonnegative_int(source.get("num_chapters_read")),
        "num_volumes_read": _nonnegative_int(source.get("num_volumes_read")),
    }


def _loads(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        parsed = {}
    return _payload(parsed if isinstance(parsed, dict) else {})


def _dumps(value: dict[str, Any]) -> str:
    return json.dumps(_payload(value), ensure_ascii=False, separators=(",", ":"))


class MalOutboundRepository:
    def __init__(self, database_manager=db):
        self.database = database_manager

    @staticmethod
    def remote_baseline(manga: dict[str, Any]) -> dict[str, Any]:
        return _payload({
            "status": manga.get("mal_status") or manga.get("library_status"),
            "score": manga.get("mal_remote_score", manga.get("user_rating")),
            "num_chapters_read": manga.get("mal_num_chapters_read"),
            "num_volumes_read": manga.get("mal_num_volumes_read"),
        })

    @staticmethod
    def local_desired(manga: dict[str, Any]) -> dict[str, Any]:
        return _payload({
            "status": manga.get("library_status"),
            "score": manga.get("user_rating"),
            "num_chapters_read": max(
                _nonnegative_int(manga.get("mal_num_chapters_read")),
                _chapter_int(manga.get("last_read_chapter_num")),
            ),
            "num_volumes_read": manga.get("mal_num_volumes_read"),
        })

    def enqueue(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        account_key: str,
        debounce_seconds: float,
    ) -> bool:
        manga_id = str(after.get("id") or "")
        mal_id = _nonnegative_int(after.get("mal_id"))
        if not manga_id or not mal_id or not account_key:
            return False
        base = self.remote_baseline(before or after)
        desired = self.local_desired(after)
        now = time.time()
        with self.database.get_connection() as conn:
            current = conn.execute(
                "SELECT * FROM mal_outbound_queue WHERE manga_id = ?",
                (manga_id,),
            ).fetchone()
            if current and str(current["account_key"]) == account_key:
                base = _loads(current["base_payload"])
            if desired == base:
                conn.execute(
                    "DELETE FROM mal_outbound_queue WHERE manga_id = ? AND account_key = ?",
                    (manga_id, account_key),
                )
                conn.commit()
                return False
            conn.execute(
                """
                INSERT INTO mal_outbound_queue (
                    manga_id, mal_id, account_key, base_payload, desired_payload,
                    remote_payload, state, queued_at, available_at, attempts,
                    last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, '{}', 'pending', ?, ?, 0, '', ?)
                ON CONFLICT(manga_id) DO UPDATE SET
                    mal_id = excluded.mal_id,
                    account_key = excluded.account_key,
                    base_payload = CASE
                        WHEN mal_outbound_queue.account_key = excluded.account_key
                        THEN mal_outbound_queue.base_payload
                        ELSE excluded.base_payload
                    END,
                    desired_payload = excluded.desired_payload,
                    remote_payload = '{}',
                    state = 'pending',
                    available_at = excluded.available_at,
                    attempts = 0,
                    last_error = '',
                    updated_at = excluded.updated_at
                """,
                (
                    manga_id, mal_id, account_key, _dumps(base), _dumps(desired),
                    int(now), now + max(0.0, debounce_seconds), int(now),
                ),
            )
            conn.commit()
        return True

    def due(self, account_key: str, now: float | None = None) -> dict[str, Any] | None:
        with self.database.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM mal_outbound_queue
                WHERE account_key = ? AND state = 'pending' AND available_at <= ?
                ORDER BY available_at, queued_at LIMIT 1
                """,
                (account_key, time.time() if now is None else float(now)),
            ).fetchone()
            return dict(row) if row else None

    def complete(self, manga_id: str, account_key: str) -> None:
        with self.database.get_connection() as conn:
            conn.execute(
                "DELETE FROM mal_outbound_queue WHERE manga_id = ? AND account_key = ?",
                (manga_id, account_key),
            )
            conn.commit()

    def retry_later(
        self,
        manga_id: str,
        account_key: str,
        *,
        delay: float,
        error: str,
    ) -> None:
        with self.database.get_connection() as conn:
            conn.execute(
                """
                UPDATE mal_outbound_queue
                SET attempts = attempts + 1, available_at = ?, last_error = ?, updated_at = ?
                WHERE manga_id = ? AND account_key = ?
                """,
                (
                    time.time() + max(1.0, delay),
                    str(error or "MyAnimeList güncellemesi gönderilemedi.")[:300],
                    int(time.time()),
                    manga_id,
                    account_key,
                ),
            )
            conn.commit()

    def mark_conflict(
        self,
        manga_id: str,
        account_key: str,
        remote: dict[str, Any],
    ) -> None:
        with self.database.get_connection() as conn:
            conn.execute(
                """
                UPDATE mal_outbound_queue
                SET state = 'conflict', remote_payload = ?, last_error = '',
                    updated_at = ?
                WHERE manga_id = ? AND account_key = ?
                """,
                (_dumps(remote), int(time.time()), manga_id, account_key),
            )
            title_row = conn.execute(
                "SELECT title FROM mangas WHERE id = ?",
                (manga_id,),
            ).fetchone()
            title = str(title_row["title"] if title_row else "Manga")
            conn.execute(
                """
                INSERT OR IGNORE INTO chapter_notifications (
                    type, title, message, manga_id, created_at, read, dedupe_key
                ) VALUES ('mal_conflict', 'MyAnimeList çakışması', ?, ?, ?, 0, ?)
                """,
                (
                    f"{title} hem MangaX'te hem MyAnimeList'te değişti. Entegrasyonlar ekranından korunacak sürümü seçin.",
                    manga_id,
                    int(time.time()),
                    f"mal_conflict:{account_key}:{manga_id}",
                ),
            )
            conn.commit()

    def pending_for_manga(self, manga_id: str) -> dict[str, Any] | None:
        with self.database.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM mal_outbound_queue WHERE manga_id = ?",
                (manga_id,),
            ).fetchone()
            return dict(row) if row else None

    def status(self, account_key: str) -> dict[str, Any]:
        if not account_key:
            return {"pending": 0, "conflicts": 0, "failed": 0, "items": []}
        with self.database.get_connection() as conn:
            rows = [
                dict(row) for row in conn.execute(
                    """
                    SELECT q.*, m.title
                    FROM mal_outbound_queue q
                    LEFT JOIN mangas m ON m.id = q.manga_id
                    WHERE q.account_key = ?
                    ORDER BY q.state DESC, q.updated_at DESC
                    """,
                    (account_key,),
                ).fetchall()
            ]
        items = [{
            "manga_id": row["manga_id"],
            "mal_id": row["mal_id"],
            "title": str(row.get("title") or "Manga"),
            "state": row["state"],
            "attempts": row["attempts"],
            "last_error": str(row["last_error"] or ""),
            "desired": _loads(row["desired_payload"]),
            "remote": _loads(row["remote_payload"]) if row["state"] == "conflict" else {},
        } for row in rows]
        return {
            "pending": sum(item["state"] == "pending" for item in items),
            "conflicts": sum(item["state"] == "conflict" for item in items),
            "failed": sum(bool(item["last_error"]) for item in items),
            "items": items,
        }

    def retry_all(self, account_key: str) -> None:
        with self.database.get_connection() as conn:
            conn.execute(
                """
                UPDATE mal_outbound_queue
                SET available_at = 0, last_error = ''
                WHERE account_key = ? AND state = 'pending'
                """,
                (account_key,),
            )
            conn.commit()

    def apply_remote_resolution(
        self,
        manga_id: str,
        account_key: str,
    ) -> bool:
        with self.database.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM mal_outbound_queue
                WHERE manga_id = ? AND account_key = ? AND state = 'conflict'
                """,
                (manga_id, account_key),
            ).fetchone()
            if not row:
                return False
            remote = _loads(row["remote_payload"])
            conn.execute(
                """
                UPDATE mangas SET library_status = ?, user_rating = ?,
                    mal_status = ?, mal_remote_score = ?,
                    mal_num_chapters_read = ?, mal_num_volumes_read = ?,
                    mal_sync_error = '', updated_at = ?
                WHERE id = ?
                """,
                (
                    remote["status"], remote["score"], remote["status"], remote["score"],
                    remote["num_chapters_read"], remote["num_volumes_read"],
                    int(time.time()), manga_id,
                ),
            )
            conn.execute(
                "DELETE FROM mal_outbound_queue WHERE manga_id = ? AND account_key = ?",
                (manga_id, account_key),
            )
            conn.execute(
                "UPDATE chapter_notifications SET read = 1 WHERE dedupe_key = ?",
                (f"mal_conflict:{account_key}:{manga_id}",),
            )
            conn.commit()
        return True

    def choose_local_resolution(self, manga_id: str, account_key: str) -> bool:
        with self.database.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM mal_outbound_queue
                WHERE manga_id = ? AND account_key = ? AND state = 'conflict'
                """,
                (manga_id, account_key),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                """
                UPDATE mal_outbound_queue
                SET base_payload = remote_payload, remote_payload = '{}',
                    state = 'pending', available_at = 0, attempts = 0,
                    last_error = '', updated_at = ?
                WHERE manga_id = ? AND account_key = ?
                """,
                (int(time.time()), manga_id, account_key),
            )
            conn.execute(
                "UPDATE chapter_notifications SET read = 1 WHERE dedupe_key = ?",
                (f"mal_conflict:{account_key}:{manga_id}",),
            )
            conn.commit()
        return True


class MalOutboundService:
    def __init__(
        self,
        integration_manager: MalIntegrationManager,
        repository: MalOutboundRepository | None = None,
        *,
        debounce_seconds: float = 3.0,
        poll_seconds: float = 0.75,
    ):
        self.integration_manager = integration_manager
        self.repository = repository or MalOutboundRepository()
        self.debounce_seconds = max(0.0, debounce_seconds)
        self.poll_seconds = max(0.05, poll_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process_lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="mangax-mal-outbound",
            daemon=True,
        )
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def enqueue_local_change(
        self,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> bool:
        if not before or not after or not self.integration_manager.two_way_sync_enabled():
            return False
        try:
            account_key = self.integration_manager.account_identity()
        except MalIntegrationError:
            return False
        return self.repository.enqueue(
            before,
            after,
            account_key=account_key,
            debounce_seconds=self.debounce_seconds,
        )

    def _account_key(self) -> str:
        if not self.integration_manager.two_way_sync_enabled():
            return ""
        try:
            return self.integration_manager.account_identity()
        except MalIntegrationError:
            return ""

    def process_due_once(self, *, now: float | None = None) -> bool:
        account_key = self._account_key()
        if not account_key or not self._process_lock.acquire(blocking=False):
            return False
        try:
            item = self.repository.due(account_key, now)
            if not item:
                return False
            manga_id = str(item["manga_id"])
            desired = _loads(item["desired_payload"])
            base = _loads(item["base_payload"])
            try:
                remote = _payload(
                    self.integration_manager.fetch_manga_list_status(item["mal_id"])
                )
                if remote == desired:
                    self._record_remote_success(manga_id, desired, "")
                    self.repository.complete(manga_id, account_key)
                    return True
                if remote != base:
                    self.repository.mark_conflict(manga_id, account_key, remote)
                    return True
                updated_response = self.integration_manager.update_manga_list_status(
                    item["mal_id"], desired
                )
                updated = _payload(updated_response)
                self._record_remote_success(
                    manga_id,
                    updated,
                    str(updated_response.get("remote_updated_at") or ""),
                )
                self.repository.complete(manga_id, account_key)
            except MalRateLimitError as error:
                self.repository.retry_later(
                    manga_id, account_key,
                    delay=error.retry_after,
                    error=str(error),
                )
            except MalIntegrationError as error:
                attempts = _nonnegative_int(item.get("attempts"))
                self.repository.retry_later(
                    manga_id, account_key,
                    delay=min(300, 5 * (2 ** min(attempts, 6))),
                    error=str(error),
                )
            return True
        finally:
            self._process_lock.release()

    def _record_remote_success(
        self,
        manga_id: str,
        value: dict[str, Any],
        remote_updated_at: str,
    ) -> None:
        normalized = _payload(value)
        with self.repository.database.get_connection() as conn:
            conn.execute(
                """
                UPDATE mangas SET mal_status = ?, mal_remote_score = ?,
                    mal_num_chapters_read = ?, mal_num_volumes_read = ?,
                    mal_remote_updated_at = CASE WHEN ? != '' THEN ? ELSE mal_remote_updated_at END,
                    mal_last_synced_at = ?, mal_sync_error = ''
                WHERE id = ?
                """,
                (
                    normalized["status"], normalized["score"],
                    normalized["num_chapters_read"], normalized["num_volumes_read"],
                    remote_updated_at, remote_updated_at, int(time.time()), manga_id,
                ),
            )
            conn.commit()

    def status(self) -> dict[str, Any]:
        account_key = self._account_key()
        result = self.repository.status(account_key)
        result["enabled"] = self.integration_manager.two_way_sync_enabled()
        return result

    def retry_all(self) -> dict[str, Any]:
        account_key = self._account_key()
        if account_key:
            self.repository.retry_all(account_key)
        return self.status()

    def resolve_conflict(self, manga_id: str, choice: str) -> dict[str, Any]:
        account_key = self._account_key()
        if not account_key:
            raise MalIntegrationError("MyAnimeList çift yönlü senkronizasyonu etkin değil.")
        if choice == "remote":
            changed = self.repository.apply_remote_resolution(manga_id, account_key)
        elif choice == "local":
            changed = self.repository.choose_local_resolution(manga_id, account_key)
        else:
            raise MalIntegrationError("Geçersiz çakışma çözümü.")
        if not changed:
            raise MalIntegrationError("Çakışma kaydı bulunamadı.")
        return self.status()

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            self.process_due_once()


mal_outbound_repository = MalOutboundRepository()
mal_outbound_service = MalOutboundService(
    mal_integration_manager,
    mal_outbound_repository,
)
