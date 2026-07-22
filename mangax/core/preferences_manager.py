"""Reader ve Full ortak kullanıcı tercihleri."""

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from mangax.core.config import DATA_DIR, DOWNLOADS_DIR, DEFAULT_DOWNLOADS_DIR

PREFERENCES_PATH = Path(DATA_DIR) / "app_preferences.json"
DEFAULT_PREFERENCES = {
    "app_theme": "dark",
    "pornhub_theme_unlocked": False,
    "nav_position": "left",
    "nav_mode": "wide",
    "nav_auto_hide": False,
    "library_density": "balanced",
    "reduce_motion": False,
    "ui_scale": "normal",
    "request_timeout_seconds": 15,
    "download_concurrency": 3,
    "low_bandwidth_mode": False,
    "image_cache_limit_mb": 512,
    "download_directory": DEFAULT_DOWNLOADS_DIR,
    "safe_mode": False,
    "extension_update_mode": "notify",
    "backup_before_extension_update": True,
    "fallback_mode": "ask",
    "catalog_provider_preference": "anilist",
    "automatic_update_checks": True,
    "last_app_update_check": "",
    "skipped_app_update_version": "",
    "onboarding_completed": None,
    "last_seen_release_notes_version": "",
    "last_run_version": "",
}


class PreferencesManager:
    def __init__(self, path: Path = PREFERENCES_PATH):
        self.path = path
        self._lock = RLock()
        self.existed_at_startup = self.path.is_file()
        self._migration_required = False
        self.data = self._load()
        if self._migration_required:
            self._save()

    def _load(self) -> dict[str, Any]:
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            merged = {**DEFAULT_PREFERENCES, **(stored if isinstance(stored, dict) else {})}
            if merged.get("catalog_provider_preference") not in {"myanimelist", "anilist"}:
                merged["catalog_provider_preference"] = "anilist"
                self._migration_required = True
            if merged.get("app_theme") not in {"dark", "light", "cover_grid", "windows_xp", "pornhub"}:
                merged["app_theme"] = "dark"
                self._migration_required = True
            layout_options = {
                "nav_position": ({"left", "right", "top", "bottom"}, "left"),
                "nav_mode": ({"wide", "compact", "icons"}, "wide"),
                "library_density": ({"comfortable", "balanced", "dense"}, "balanced"),
                "ui_scale": ({"small", "normal", "large"}, "normal"),
            }
            for key, (allowed, fallback) in layout_options.items():
                if merged.get(key) not in allowed:
                    merged[key] = fallback
                    self._migration_required = True
            for key in ("nav_auto_hide", "reduce_motion", "pornhub_theme_unlocked"):
                if not isinstance(merged.get(key), bool):
                    merged[key] = False
                    self._migration_required = True
            return merged
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
            for key in ("low_bandwidth_mode", "safe_mode", "backup_before_extension_update", "automatic_update_checks", "nav_auto_hide", "reduce_motion", "pornhub_theme_unlocked"):
                if key in normalized:
                    normalized[key] = bool(normalized[key])
            if normalized.get("extension_update_mode", "notify") not in {"manual", "notify", "auto"}:
                raise ValueError("Geçersiz eklenti güncelleme modu")
            if normalized.get("fallback_mode", "ask") not in {"auto", "ask", "off"}:
                raise ValueError("Geçersiz kaynak geçiş modu")
            if normalized.get("catalog_provider_preference", "anilist") not in {"myanimelist", "anilist"}:
                raise ValueError("Geçersiz katalog sağlayıcısı tercihi")
            if normalized.get("app_theme", "dark") not in {"dark", "light", "cover_grid", "windows_xp", "pornhub"}:
                raise ValueError("Geçersiz uygulama teması")
            allowed_layout_values = {
                "nav_position": {"left", "right", "top", "bottom"},
                "nav_mode": {"wide", "compact", "icons"},
                "library_density": {"comfortable", "balanced", "dense"},
                "ui_scale": {"small", "normal", "large"},
            }
            for key, allowed in allowed_layout_values.items():
                if key in normalized and normalized[key] not in allowed:
                    raise ValueError(f"Geçersiz görünüm tercihi: {key}")
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
            pornhub_theme_unlocked = self.data.get("pornhub_theme_unlocked") is True
            self.data = dict(DEFAULT_PREFERENCES)
            self.data["pornhub_theme_unlocked"] = pornhub_theme_unlocked
            self._save()
            return dict(self.data)

    def startup_experience(
        self,
        *,
        current_version: str,
        edition: str,
        has_existing_data: bool = False,
        legacy_completed: bool = False,
    ) -> dict[str, Any]:
        from mangax.core.release_notes import release_notes_for

        with self._lock:
            existing_install = bool(self.existed_at_startup or has_existing_data or legacy_completed)
            completed_value = self.data.get("onboarding_completed")
            changed = False
            if not isinstance(completed_value, bool):
                completed_value = existing_install
                self.data["onboarding_completed"] = completed_value
                changed = True
            previous_version = str(self.data.get("last_run_version") or "")
            last_seen = str(self.data.get("last_seen_release_notes_version") or "")
            notes = release_notes_for(current_version, edition)
            show_release_notes = bool(
                completed_value
                and existing_install
                and notes
                and previous_version
                and previous_version != current_version
                and last_seen != current_version
            )
            if completed_value and not notes and last_seen != current_version:
                self.data["last_seen_release_notes_version"] = current_version
                changed = True
            if previous_version != current_version:
                self.data["last_run_version"] = current_version
                changed = True
            if changed:
                self._save()
            return {
                "onboarding_completed": completed_value,
                "show_onboarding": not completed_value,
                "show_release_notes": show_release_notes,
                "release_notes": notes if show_release_notes else None,
                "current_version": current_version,
                "previous_version": previous_version,
                "edition": edition,
            }

    def complete_onboarding(self, current_version: str) -> dict[str, Any]:
        with self._lock:
            self.data["onboarding_completed"] = True
            self.data["last_run_version"] = current_version
            self.data["last_seen_release_notes_version"] = current_version
            self._save()
            return dict(self.data)

    def mark_release_notes_seen(self, version: str) -> dict[str, Any]:
        with self._lock:
            self.data["last_seen_release_notes_version"] = str(version or "").strip()
            self._save()
            return dict(self.data)


preferences_manager = PreferencesManager()
