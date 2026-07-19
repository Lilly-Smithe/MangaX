"""Reader ve Full için ortak, açılışı bloklamayan MAL senkron planlayıcısı."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from mangax.integrations.mal_integration import (
    MalIntegrationError,
    MalIntegrationManager,
    mal_integration_manager,
)
from mangax.integrations.mal_sync_jobs import (
    ACTIVE_MAL_SYNC_STATES,
    MalSyncJobError,
    MalSyncJobManager,
    mal_sync_job_manager,
)


MAL_SYNC_INTERVAL_SECONDS = {
    "startup": 0,
    "6h": 6 * 60 * 60,
    "12h": 12 * 60 * 60,
    "24h": 24 * 60 * 60,
}
DEFAULT_MAL_SYNC_INTERVAL = "24h"


class MalSyncScheduler:
    """Yerel tercihe göre MAL iş yöneticisini periyodik olarak tetikler."""

    def __init__(
        self,
        integration_manager: MalIntegrationManager,
        job_manager: MalSyncJobManager,
        *,
        poll_seconds: float = 30.0,
        retry_delays: tuple[float, ...] = (30.0, 120.0),
        clock: Callable[[], float] = time.time,
    ):
        self.integration_manager = integration_manager
        self.job_manager = job_manager
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.retry_delays = tuple(max(0.0, float(value)) for value in retry_delays)
        self.clock = clock
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._shutting_down = False
        self._startup_pending = True
        self._observed_job_id = ""
        self._retry_attempt = 0
        self._retry_at = 0.0
        self._next_regular_at = 0.0

    def start(self) -> None:
        """Yalnız daemon planlayıcıyı başlatır; ağ isteğini çağıran thread'de çalıştırmaz."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._shutting_down = False
            self._startup_pending = True
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="MangaXMalScheduler",
                daemon=True,
            )
            self._thread.start()

    def preferences_changed(self) -> None:
        self._wake_event.set()

    def shutdown(self, timeout: float = 2.0) -> None:
        with self._lock:
            self._shutting_down = True
            thread = self._thread
            self._stop_event.set()
            self._wake_event.set()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.evaluate()
            except (MalIntegrationError, MalSyncJobError, OSError, ValueError, TypeError):
                # Planlayıcı hiçbir koşulda uygulama açılışını veya ana yaşam döngüsünü düşürmez.
                pass
            self._wake_event.wait(self.poll_seconds)
            self._wake_event.clear()

    @staticmethod
    def _timestamp(summary: Any) -> float:
        if not isinstance(summary, dict):
            return 0.0
        try:
            return max(0.0, float(summary.get("completed_at") or 0))
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def _start_job(self, now: float, trigger: str) -> str:
        if self._shutting_down or self._stop_event.is_set():
            return "stopping"
        job = self.job_manager.start(trigger=trigger)
        if job.get("already_running"):
            return "already_running"
        self._observed_job_id = str(job.get("job_id") or "")
        self._next_regular_at = 0.0
        if trigger == "automatic":
            self._startup_pending = False
            self._retry_attempt = 0
        return "started"

    def _observe_terminal_job(self, job: dict[str, Any], now: float, interval_seconds: int) -> str | None:
        if not self._observed_job_id or job.get("job_id") != self._observed_job_id:
            return None
        state = str(job.get("status") or "")
        if state in ACTIVE_MAL_SYNC_STATES:
            return "active"
        self._observed_job_id = ""
        if state == "completed":
            self._retry_attempt = 0
            self._retry_at = 0.0
            self._next_regular_at = 0.0
            return "completed"
        if state == "failed" and job.get("retryable") and self._retry_attempt < len(self.retry_delays):
            delay = self.retry_delays[self._retry_attempt]
            self._retry_attempt += 1
            self._retry_at = now + delay
            return "retry_scheduled"
        self._retry_at = 0.0
        self._retry_attempt = 0
        self._next_regular_at = now + (interval_seconds or MAL_SYNC_INTERVAL_SECONDS["24h"])
        return state or "finished"

    def evaluate(self, *, now: float | None = None) -> str:
        """Tek planlama turu; testlerde saat ilerletilerek doğrudan çağrılabilir."""
        with self._lock:
            if self._shutting_down or self._stop_event.is_set():
                return "stopping"
            current_time = float(self.clock() if now is None else now)
            status = self.integration_manager.status()
            if not status.get("connected") or status.get("automatic_sync") is False:
                self._retry_at = 0.0
                self._retry_attempt = 0
                return "disabled"

            interval = str(status.get("sync_interval") or DEFAULT_MAL_SYNC_INTERVAL)
            interval_seconds = MAL_SYNC_INTERVAL_SECONDS.get(
                interval,
                MAL_SYNC_INTERVAL_SECONDS[DEFAULT_MAL_SYNC_INTERVAL],
            )
            job = self.job_manager.status()
            observed = self._observe_terminal_job(job, current_time, interval_seconds)
            if observed:
                return observed
            if str(job.get("status") or "") in ACTIVE_MAL_SYNC_STATES:
                return "active"

            if self._retry_at:
                if current_time < self._retry_at:
                    return "retry_wait"
                self._retry_at = 0.0
                return self._start_job(current_time, "automatic_retry")

            if self._next_regular_at and current_time < self._next_regular_at:
                return "fresh"
            if interval == "startup":
                if not self._startup_pending:
                    return "fresh"
            else:
                last_success = self._timestamp(status.get("last_success"))
                if last_success and current_time - last_success < interval_seconds:
                    return "fresh"
            return self._start_job(current_time, "automatic")


mal_sync_scheduler = MalSyncScheduler(
    integration_manager=mal_integration_manager,
    job_manager=mal_sync_job_manager,
)
