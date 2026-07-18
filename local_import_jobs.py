"""Yerel manga aktarımlarını WebView iş parçacığını kilitlemeden yönetir."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from local_importer import ImportCancelled, import_local_manga


class LocalImportJobManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}

    def start(self, selected_path: str) -> dict:
        job_id = uuid.uuid4().hex
        cancel_event = threading.Event()
        job = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0,
            "current": 0,
            "total": 0,
            "message": "Manga hazırlanıyor…",
            "title": Path(selected_path).stem,
            "cancel_event": cancel_event,
            "created_at": time.time(),
        }
        with self._lock:
            self._jobs[job_id] = job
            self._prune_locked()
        threading.Thread(
            target=self._run,
            args=(job_id, str(selected_path), cancel_event),
            name=f"MangaXLocalImport-{job_id[:8]}",
            daemon=True,
        ).start()
        return self.status(job_id)

    def _run(self, job_id: str, selected_path: str, cancel_event: threading.Event) -> None:
        self._update(job_id, status="running")

        def progress(current: int, total: int, message: str) -> None:
            percent = int((current / total) * 100) if total else 0
            self._update(
                job_id,
                current=current,
                total=total,
                progress=max(0, min(99, percent)),
                message=message,
            )

        try:
            result = import_local_manga(
                selected_path,
                progress_callback=progress,
                cancel_event=cancel_event,
            )
            manga = result.get("manga") or {}
            self._update(
                job_id,
                status="success",
                progress=100,
                message="Manga kütüphaneye eklendi.",
                manga={"id": manga.get("id"), "title": manga.get("title")},
                chapter_count=result.get("chapter_count", 0),
            )
        except ImportCancelled as error:
            self._update(job_id, status="cancelled", message=str(error))
        except Exception as error:
            self._update(job_id, status="error", message=str(error))

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(str(job_id))
            if not job:
                return {"status": "missing", "message": "Aktarım işi bulunamadı."}
            if job["status"] in {"queued", "running"}:
                job["cancel_event"].set()
                job["message"] = "İptal ediliyor…"
            return self._public(job)

    def status(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(str(job_id))
            return self._public(job) if job else {"status": "missing", "message": "Aktarım işi bulunamadı."}

    def _update(self, job_id: str, **values) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(values)

    def _prune_locked(self) -> None:
        finished = sorted(
            (job for job in self._jobs.values() if job["status"] not in {"queued", "running"}),
            key=lambda job: job["created_at"],
        )
        for job in finished[:-20]:
            self._jobs.pop(job["job_id"], None)

    @staticmethod
    def _public(job: dict) -> dict:
        return {key: value for key, value in job.items() if key not in {"cancel_event", "created_at"}}
