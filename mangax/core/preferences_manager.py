"""Reader ve Full ortak kullanıcı tercihleri."""

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from mangax.core.config import DATA_DIR, DOWNLOADS_DIR, DEFAULT_DOWNLOADS_DIR

PREFERENCES_PATH = Path(DATA_DIR) / "app_preferences.json"
DEFAULT_PREFERENCES = {
    "request_timeout_seconds": 15,
    "download_concurrency": 3,
    "low_bandwidth_mode": False,
    "image_cache_limit_mb": 512,
    "download_directory": DEFAULT_DOWNLOADS_DIR,
    "safe_mode": False,
    "extension_update_mode": "notify",
    "backup_before_extension_update": True,
    "fallback_mode": "ask",
    "automatic_update_checks": True,
}


class PreferencesManager:
    def __init__(self, path: Path = PREFERENCES_PATH):
        self.path = path
        self._lock = RLock()
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            return {**DEFAULT_PREFERENCES, **(stored if isinstance(stored, dict) else {})}
        except (OSError, ValueError, TypeError):
            return dict(DEFAULT_PREFERENCES)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def get_all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.data)

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            normalized = dict(values)
            if "request_timeout_seconds" in normalized:
                normalized["request_timeout_seconds"] = max(5, min(60, int(normalized["request_timeout_seconds"])))
            if "download_concurrency" in normalized:
                normalized["download_concurrency"] = max(1, min(8, int(normalized["download_concurrency"])))
            if "image_cache_limit_mb" in normalized:
                normalized["image_cache_limit_mb"] = max(64, min(4096, int(normalized["image_cache_limit_mb"])))
            for key in ("low_bandwidth_mode", "safe_mode", "backup_before_extension_update", "automatic_update_checks"):
                if key in normalized:
                    normalized[key] = bool(normalized[key])
            if normalized.get("extension_update_mode", "notify") not in {"manual", "notify", "auto"}:
                raise ValueError("Geçersiz eklenti güncelleme modu")
            if normalized.get("fallback_mode", "ask") not in {"auto", "ask", "off"}:
                raise ValueError("Geçersiz kaynak geçiş modu")
            if "download_directory" in normalized:
                path = Path(str(normalized["download_directory"] or DOWNLOADS_DIR)).expanduser().resolve()
                if path == Path(path.anchor):
                    raise ValueError("Disk kökü indirme klasörü olarak kullanılamaz")
                normalized["download_directory"] = str(path)
            self.data.update({key: value for key, value in normalized.items() if key in DEFAULT_PREFERENCES})
            self._save()
            return dict(self.data)

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self.data = dict(DEFAULT_PREFERENCES)
            self._save()
            return dict(self.data)


preferences_manager = PreferencesManager()
