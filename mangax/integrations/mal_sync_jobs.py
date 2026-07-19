"""MyAnimeList kütüphane senkronizasyonunu arka planda yöneten iş katmanı."""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any

from mangax.integrations.mal_integration import (
    MalIntegrationError,
    MalIntegrationManager,
    mal_integration_manager,
)
from mangax.integrations.mal_sync import (
    MalSyncCancelled,
    MalSyncService,
    mal_sync_service,
)


ACTIVE_MAL_SYNC_STATES = {"pending", "fetching", "matching", "importing"}
TERMINAL_MAL_SYNC_STATES = {"completed", "failed", "cancelled"}


class MalSyncJobError(RuntimeError):
    pass


class MalSyncJobManager:
    def __init__(
        self,
        integration_manager: MalIntegrationManager,
        sync_service: MalSyncService,
    ):
        self.integration_manager = integration_manager
        self.sync_service = sync_service
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._current_job_id = ""
        self._last_summary: dict[str, Any] = {}
        self._shutting_down = False

    def start_lifecycle(self) -> None:
        with self._lock:
            self._shutting_down = False

    def start(self, *, trigger: str = "manual") -> dict[str, Any]:
        status = self.integration_manager.status()
        if not status.get("connected"):
            raise MalSyncJobError("MyAnimeList hesabı bağlı değil.")
        account_key = self.integration_manager.account_identity()
        if not account_key:
            raise MalSyncJobError("Bağlı MyAnimeList hesabı doğrulanamadı.")

        with self._lock:
            if self._shutting_down:
                raise MalSyncJobError("MangaX kapanırken yeni senkronizasyon başlatılamaz.")
            current = self._jobs.get(self._current_job_id)
            if current and current["status"] in ACTIVE_MAL_SYNC_STATES:
                response = self._public(current)
                response["already_running"] = True
                return response

            job_id = secrets.token_urlsafe(18)
            cancel_event = threading.Event()
            job = {
                "job_id": job_id,
                "account_key": account_key,
                "trigger": trigger if trigger in {"oauth", "automatic", "automatic_retry"} else "manual",
                "status": "pending",
                "processed": 0,
                "total": 0,
                "added": 0,
                "updated": 0,
                "unchanged": 0,
                "unmatched": 0,
                "skipped": 0,
                "failed": 0,
                "message": "MyAnimeList senkronizasyonu sıraya alındı.",
                "error": "",
                "retryable": False,
                "created_at": int(time.time()),
                "completed_at": 0,
                "cancel_event": cancel_event,
                "thread": None,
            }
            self._jobs[job_id] = job
            self._current_job_id = job_id
            thread = threading.Thread(
                target=self._run,
                args=(job_id,),
                name=f"MangaXMalSync-{job_id[:8]}",
                daemon=True,
            )
            job["thread"] = thread
            thread.start()
            return self._public(job)

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            cancel_event = job["cancel_event"]

        def progress(
            stage: str,
            processed: int,
            total: int,
            counts: dict[str, int],
        ) -> None:
            messages = {
                "fetching": "MyAnimeList manga listesi alınıyor…",
                "matching": "Manga kimlikleri eşleştiriliyor…",
                "importing": "MangaX kütüphanesi güncelleniyor…",
            }
            self._update(
                job_id,
                status=stage,
                processed=max(0, int(processed or 0)),
                total=max(0, int(total or 0)),
                added=max(0, int(counts.get("added") or 0)),
                updated=max(0, int(counts.get("updated") or 0)),
                unchanged=max(0, int(counts.get("unchanged") or 0)),
                unmatched=max(0, int(counts.get("unmatched") or 0)),
                skipped=max(
                    0,
                    int(counts.get("unchanged") or 0)
                    + int(counts.get("unmatched") or 0),
                ),
                failed=max(0, int(counts.get("failed") or 0)),
                message=messages.get(stage, "MyAnimeList senkronizasyonu sürüyor…"),
            )

        try:
            result = self.sync_service.sync(
                progress_callback=progress,
                cancel_event=cancel_event,
            )
            summary = {
                "job_id": job_id,
                "status": "completed",
                "processed": max(0, int(result.get("total") or 0)),
                "total": max(0, int(result.get("total") or 0)),
                "added": max(0, int(result.get("added") or 0)),
                "updated": max(0, int(result.get("updated") or 0)),
                "unchanged": max(0, int(result.get("unchanged") or 0)),
                "unmatched": max(0, int(result.get("unmatched") or 0)),
                "skipped": max(
                    0,
                    int(result.get("unchanged") or 0)
                    + int(result.get("unmatched") or 0),
                ),
                "failed": max(0, int(result.get("failed") or 0)),
                "message": "MyAnimeList kütüphanesi eşitlendi.",
                "error": "",
                "retryable": False,
                "completed_at": int(time.time()),
            }
            self._finish(job_id, summary)
        except MalSyncCancelled:
            self._finish(job_id, {
                "job_id": job_id,
                "status": "cancelled",
                "message": "MyAnimeList senkronizasyonu iptal edildi.",
                "error": "",
                "retryable": False,
                "completed_at": int(time.time()),
            })
        except MalIntegrationError:
            self._finish(job_id, {
                "job_id": job_id,
                "status": "failed",
                "message": "MyAnimeList senkronizasyonu tamamlanamadı.",
                "error": "MyAnimeList hizmetine şu anda ulaşılamıyor.",
                "retryable": True,
                "completed_at": int(time.time()),
            })
        except Exception:
            # Beklenmeyen istisnanın metni token veya istek ayrıntısı taşıyabilir;
            # yalnız sabit ve kullanıcı dostu hata metni dışarı açılır.
            self._finish(job_id, {
                "job_id": job_id,
                "status": "failed",
                "message": "MyAnimeList senkronizasyonu tamamlanamadı.",
                "error": "Kütüphane eşitlenirken beklenmeyen bir hata oluştu.",
                "retryable": False,
                "completed_at": int(time.time()),
            })

    def _finish(self, job_id: str, values: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(values)
            summary = self._public(job)
            self._last_summary = summary
            record_summary = getattr(self.integration_manager, "record_sync_summary", None)
            if callable(record_summary):
                try:
                    record_summary(summary)
                except (OSError, ValueError, TypeError):
                    # Özet ikincil kullanıcı arayüzü verisidir; senkronizasyon sonucunu
                    # veya arka plan işinin terminal duruma geçmesini bozmamalıdır.
                    pass

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(self._current_job_id)
            if not job:
                return self.idle_status()
            if job["status"] in ACTIVE_MAL_SYNC_STATES:
                job["cancel_event"].set()
                job["message"] = "Senkronizasyon güvenli şekilde iptal ediliyor…"
            return self._public(job)

    def status(self) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(self._current_job_id)
            return self._public(job) if job else self.idle_status()

    def last_summary(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_summary) if self._last_summary else self.idle_status()

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._shutting_down = True
            job = self._jobs.get(self._current_job_id)
            thread = job.get("thread") if job else None
            if job and job["status"] in ACTIVE_MAL_SYNC_STATES:
                job["cancel_event"].set()
                job["message"] = "Uygulama kapanırken senkronizasyon durduruluyor…"
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(values)

    @staticmethod
    def idle_status() -> dict[str, Any]:
        return {
            "job_id": "",
            "status": "idle",
            "processed": 0,
            "total": 0,
            "added": 0,
            "updated": 0,
            "unchanged": 0,
            "unmatched": 0,
            "skipped": 0,
            "failed": 0,
            "message": "Henüz bir MyAnimeList senkronizasyonu çalışmadı.",
            "error": "",
            "retryable": False,
            "completed_at": 0,
        }

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in job.items()
            if key not in {"cancel_event", "thread", "account_key"}
        }


mal_sync_job_manager = MalSyncJobManager(
    integration_manager=mal_integration_manager,
    sync_service=mal_sync_service,
)
