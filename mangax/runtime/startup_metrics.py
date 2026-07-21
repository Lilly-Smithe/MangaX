"""Process-local, privacy-safe startup timing markers.

Detailed output is opt-in through ``MANGAX_PROFILE_STARTUP=1``. The in-memory
snapshot stores only fixed marker names and monotonic durations; tokens, URLs,
user content and filesystem paths never enter this collector.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any


class StartupMetrics:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started_at = time.perf_counter()
        self._marks: dict[str, float] = {"process_started": 0.0}
        self._enabled = os.getenv("MANGAX_PROFILE_STARTUP", "").strip() == "1"

    def reset(self, *, started_at: float | None = None) -> None:
        with self._lock:
            self._started_at = float(started_at or time.perf_counter())
            self._marks = {"process_started": 0.0}

    def mark(self, name: str) -> float:
        safe_name = str(name or "").strip()
        if not safe_name or not safe_name.replace("_", "").isalnum():
            raise ValueError("Geçersiz başlangıç ölçüm işareti.")
        elapsed = max(0.0, time.perf_counter() - self._started_at)
        with self._lock:
            self._marks.setdefault(safe_name, elapsed)
            value = self._marks[safe_name]
        if self._enabled:
            print(f"[MangaX PERF] {safe_name}={value:.4f}s", flush=True)
        return value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            marks = dict(self._marks)
        ordered = dict(sorted(marks.items(), key=lambda item: item[1]))
        return {
            "enabled": self._enabled,
            "elapsed_seconds": round(max(ordered.values(), default=0.0), 6),
            "marks": {name: round(value, 6) for name, value in ordered.items()},
        }


startup_metrics = StartupMetrics()
