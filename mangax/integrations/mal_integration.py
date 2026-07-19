"""Reader ve Full tarafından paylaşılan MyAnimeList OAuth2/PKCE entegrasyonu."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import httpx

from mangax.core.config import APP_URL, DATA_DIR, MAL_OAUTH_CLIENT_ID
from mangax.integrations.secure_store import SecureStoreError, WindowsDpapiJsonStore


MAL_AUTHORIZE_URL = "https://myanimelist.net/v1/oauth2/authorize"
MAL_TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"
MAL_API_URL = "https://api.myanimelist.net/v2"
MAL_CALLBACK_URL = f"{APP_URL}/api/integrations/mal/callback"
MAL_CONFIG_PATH = Path(DATA_DIR) / "mal_integration.json"
MAL_CREDENTIALS_PATH = Path(DATA_DIR) / ".mal_credentials"
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,160}$")
MAX_MAL_LIST_PAGES = 1000
MAL_SYNC_INTERVALS = {"startup", "6h", "12h", "24h"}
DEFAULT_MAL_SYNC_INTERVAL = "24h"


class MalIntegrationError(RuntimeError):
    pass


class MalRateLimitError(MalIntegrationError):
    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = max(1, min(3600, int(retry_after or 60)))


class MalIntegrationManager:
    def __init__(
        self,
        config_path: Path = MAL_CONFIG_PATH,
        credential_store: WindowsDpapiJsonStore | None = None,
        manga_matcher: Callable[[list[int]], dict[int, dict[str, Any]]] | None = None,
    ):
        self.config_path = Path(config_path)
        self.credential_store = credential_store or WindowsDpapiJsonStore(
            MAL_CREDENTIALS_PATH, "MangaX MyAnimeList hesabı"
        )
        self._lock = threading.RLock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._preview: dict[str, Any] = {"saved_at": 0.0, "entries": []}
        self._manga_matcher = manga_matcher

    def set_manga_matcher(
        self,
        matcher: Callable[[list[int]], dict[int, dict[str, Any]]] | None,
    ) -> None:
        """Full edition AniList eşleştiricisini çalışma zamanında enjekte eder."""
        with self._lock:
            self._manga_matcher = matcher
            self._preview = {"saved_at": 0.0, "entries": []}

    @staticmethod
    def _reader_manga(node: dict[str, Any], mal_id: int) -> dict[str, Any]:
        picture = node.get("main_picture") if isinstance(node.get("main_picture"), dict) else {}
        year_text = str(node.get("start_date") or "")[:4]
        return {
            "id": f"mal_{mal_id}",
            "title": str(node.get("title") or "Bilinmeyen Manga"),
            "description": "",
            "cover_url": str(picture.get("large") or picture.get("medium") or ""),
            "status": str(node.get("status") or "unknown"),
            "tags": [],
            "year": int(year_text) if year_text.isdigit() else 0,
        }

    def _load_config(self) -> dict[str, Any]:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_config(self, value: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.config_path)

    def _load_credentials(self) -> dict[str, Any]:
        try:
            return self.credential_store.load()
        except SecureStoreError as error:
            raise MalIntegrationError(str(error)) from error

    def _save_credentials(self, value: dict[str, Any]) -> None:
        try:
            self.credential_store.save(value)
        except SecureStoreError as error:
            raise MalIntegrationError(str(error)) from error

    def get_client_id(self) -> str:
        return str(
            os.getenv("MANGAX_MAL_CLIENT_ID")
            or self._load_config().get("client_id")
            or MAL_OAUTH_CLIENT_ID
            or ""
        ).strip()

    def configure(self, client_id: str, client_secret: str = "") -> dict[str, Any]:
        normalized = str(client_id or "").strip()
        if not CLIENT_ID_PATTERN.fullmatch(normalized):
            raise MalIntegrationError("Geçerli bir MyAnimeList Client ID girilmelidir.")
        with self._lock:
            config = self._load_config()
            config["client_id"] = normalized
            self._save_config(config)
        if client_secret:
            credentials = self._load_credentials()
            credentials["client_secret"] = str(client_secret).strip()
            self._save_credentials(credentials)
        return self.status()

    def automatic_sync_enabled(self) -> bool:
        return self._load_config().get("automatic_sync", True) is not False

    def two_way_sync_enabled(self) -> bool:
        # Uzak hesapta yazma yapan özellik güvenli varsayılan olarak kapalıdır.
        return self._load_config().get("two_way_sync") is True

    def set_sync_preferences(
        self,
        enabled: bool,
        interval: str,
        two_way_sync: bool | None = None,
    ) -> dict[str, Any]:
        normalized_interval = str(interval or "").strip().lower()
        if normalized_interval not in MAL_SYNC_INTERVALS:
            raise MalIntegrationError("Geçerli bir MyAnimeList senkronizasyon aralığı seçilmelidir.")
        with self._lock:
            config = self._load_config()
            config["automatic_sync"] = bool(enabled)
            config["sync_interval"] = normalized_interval
            if two_way_sync is not None:
                config["two_way_sync"] = bool(two_way_sync)
            self._save_config(config)
        return self.status()

    def set_automatic_sync(self, enabled: bool) -> dict[str, Any]:
        return self.set_sync_preferences(
            enabled,
            str(self._load_config().get("sync_interval") or DEFAULT_MAL_SYNC_INTERVAL),
        )

    def record_sync_summary(self, summary: dict[str, Any]) -> None:
        allowed = {
            "status", "total", "added", "updated", "unchanged",
            "unmatched", "failed", "completed_at", "error",
        }
        public_summary = {key: summary.get(key) for key in allowed if key in summary}
        if public_summary.get("status") not in {"completed", "failed", "cancelled"}:
            return
        with self._lock:
            config = self._load_config()
            config["last_sync"] = public_summary
            if public_summary.get("status") == "completed":
                config["last_success"] = public_summary
                config.pop("last_error", None)
            elif public_summary.get("status") == "failed":
                config["last_error"] = {
                    "error": str(public_summary.get("error") or "MyAnimeList eşitlemesi tamamlanamadı.")[:300],
                    "completed_at": public_summary.get("completed_at"),
                }
            self._save_config(config)

    def status(self) -> dict[str, Any]:
        client_id = self.get_client_id()
        try:
            credentials = self._load_credentials()
            storage_available = True
            storage_error = ""
        except MalIntegrationError as error:
            credentials = {}
            storage_available = False
            storage_error = str(error)
        profile = credentials.get("profile") if isinstance(credentials.get("profile"), dict) else {}
        config = self._load_config()
        last_sync = config.get("last_sync") if isinstance(config.get("last_sync"), dict) else {}
        last_success = config.get("last_success") if isinstance(config.get("last_success"), dict) else {}
        if not last_success and last_sync.get("status") == "completed":
            last_success = last_sync
        last_error = config.get("last_error") if isinstance(config.get("last_error"), dict) else {}
        return {
            "configured": bool(client_id),
            "client_id": client_id,
            "connected": bool(credentials.get("access_token")),
            "username": str(profile.get("name") or ""),
            "callback_url": MAL_CALLBACK_URL,
            "automatic_sync": self.automatic_sync_enabled(),
            "two_way_sync": self.two_way_sync_enabled(),
            "sync_interval": (
                str(config.get("sync_interval") or DEFAULT_MAL_SYNC_INTERVAL)
                if str(config.get("sync_interval") or DEFAULT_MAL_SYNC_INTERVAL) in MAL_SYNC_INTERVALS
                else DEFAULT_MAL_SYNC_INTERVAL
            ),
            "last_sync": last_sync,
            "last_success": last_success,
            "last_error": last_error,
            "secure_storage": storage_available,
            "storage_error": storage_error,
        }

    def account_identity(self) -> str:
        """Tokenı açığa çıkarmadan bağlı hesabın kararlı iş anahtarını döndürür."""
        credentials = self._load_credentials()
        profile = credentials.get("profile") if isinstance(credentials.get("profile"), dict) else {}
        account_id = str(profile.get("id") or "").strip()
        username = str(profile.get("name") or "").strip().casefold()
        return account_id or username

    def start_oauth(self) -> str:
        client_id = self.get_client_id()
        if not client_id:
            raise MalIntegrationError("Önce MyAnimeList Client ID kaydedilmelidir.")
        verifier = secrets.token_urlsafe(72)[:128]
        state = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._pending = {
                key: value for key, value in self._pending.items()
                if now - float(value.get("created_at") or 0) < 600
            }
            self._pending[state] = {"verifier": verifier, "created_at": now, "client_id": client_id}
        query = urlencode({
            "response_type": "code",
            "client_id": client_id,
            "code_challenge": verifier,
            "code_challenge_method": "plain",
            "scope": "write:users",
            "state": state,
            "redirect_uri": MAL_CALLBACK_URL,
        })
        return f"{MAL_AUTHORIZE_URL}?{query}"

    def complete_oauth(self, state: str, code: str) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.pop(str(state or ""), None)
        if not pending or time.time() - float(pending.get("created_at") or 0) > 600:
            raise MalIntegrationError("MAL bağlantı isteğinin süresi doldu. Ayarlardan yeniden deneyin.")
        credentials = self._load_credentials()
        payload = {
            "client_id": pending["client_id"],
            "grant_type": "authorization_code",
            "code": str(code or ""),
            "code_verifier": pending["verifier"],
            "redirect_uri": MAL_CALLBACK_URL,
        }
        if credentials.get("client_secret"):
            payload["client_secret"] = credentials["client_secret"]
        try:
            response = httpx.post(MAL_TOKEN_URL, data=payload, timeout=20.0)
            response.raise_for_status()
            token = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise MalIntegrationError("MyAnimeList erişim izni alınamadı. Client ID ve dönüş adresini kontrol edin.") from error
        credentials.update({
            "access_token": str(token.get("access_token") or ""),
            "refresh_token": str(token.get("refresh_token") or ""),
            "expires_at": int(time.time()) + max(60, int(token.get("expires_in") or 3600)),
        })
        if not credentials["access_token"]:
            raise MalIntegrationError("MyAnimeList geçerli bir erişim anahtarı göndermedi.")
        self._save_credentials(credentials)
        profile = self._authorized_get(f"{MAL_API_URL}/users/@me")
        credentials["profile"] = {"id": profile.get("id"), "name": profile.get("name")}
        self._save_credentials(credentials)
        return self.status()

    def _refresh_access_token(self, credentials: dict[str, Any]) -> dict[str, Any]:
        refresh_token = str(credentials.get("refresh_token") or "")
        if not refresh_token:
            raise MalIntegrationError("MAL oturumunun süresi doldu. Hesabı yeniden bağlayın.")
        payload = {
            "client_id": self.get_client_id(),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        if credentials.get("client_secret"):
            payload["client_secret"] = credentials["client_secret"]
        try:
            response = httpx.post(MAL_TOKEN_URL, data=payload, timeout=20.0)
            response.raise_for_status()
            token = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise MalIntegrationError("MAL oturumu yenilenemedi. Hesabı yeniden bağlayın.") from error
        credentials.update({
            "access_token": str(token.get("access_token") or ""),
            "refresh_token": str(token.get("refresh_token") or refresh_token),
            "expires_at": int(time.time()) + max(60, int(token.get("expires_in") or 3600)),
        })
        self._save_credentials(credentials)
        return credentials

    @staticmethod
    def _safe_retry_after(response: httpx.Response) -> int:
        try:
            return max(1, min(3600, int(response.headers.get("Retry-After") or 60)))
        except (TypeError, ValueError):
            return 60

    def _authorized_request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        credentials = self._load_credentials()
        if not credentials.get("access_token"):
            raise MalIntegrationError("MyAnimeList hesabı bağlı değil.")
        if int(credentials.get("expires_at") or 0) <= int(time.time()) + 60:
            credentials = self._refresh_access_token(credentials)
        def send(active_credentials: dict[str, Any]) -> httpx.Response:
            options = {
                "headers": {"Authorization": f"Bearer {active_credentials['access_token']}"},
                "timeout": 25.0,
                "follow_redirects": True,
            }
            if method.upper() == "GET":
                return httpx.get(url, **options)
            return httpx.request(method, url, data=data, **options)
        try:
            response = send(credentials)
            if response.status_code == 401:
                credentials = self._refresh_access_token(credentials)
                response = send(credentials)
            if response.status_code == 429:
                raise MalRateLimitError(
                    "MyAnimeList istek sınırına ulaşıldı; değişiklik daha sonra yeniden denenecek.",
                    self._safe_retry_after(response),
                )
            response.raise_for_status()
            value = response.json()
            return value if isinstance(value, dict) else {}
        except MalIntegrationError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise MalIntegrationError("MyAnimeList listesine şu anda ulaşılamıyor.") from error

    def _authorized_get(self, url: str) -> dict[str, Any]:
        return self._authorized_request("GET", url)

    @classmethod
    def _normalized_list_status(cls, value: Any) -> dict[str, Any]:
        status = cls._mapping(value)
        return {
            "status": str(status.get("status") or "plan_to_read"),
            "score": min(10, cls._safe_nonnegative_int(status.get("score"))),
            "num_chapters_read": cls._safe_nonnegative_int(status.get("num_chapters_read")),
            "num_volumes_read": cls._safe_nonnegative_int(status.get("num_volumes_read")),
            "remote_updated_at": str(status.get("updated_at") or "")[:80],
        }

    def fetch_manga_list_status(self, mal_id: int) -> dict[str, Any]:
        safe_id = self._safe_nonnegative_int(mal_id)
        if not safe_id:
            raise MalIntegrationError("Geçersiz MyAnimeList manga kimliği.")
        response = self._authorized_get(
            f"{MAL_API_URL}/manga/{safe_id}?{urlencode({'fields': 'my_list_status'})}"
        )
        return self._normalized_list_status(response.get("my_list_status"))

    def update_manga_list_status(
        self,
        mal_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Resmî MAL sözleşmesine yalnızca izin verilen liste alanlarını gönder."""
        safe_id = self._safe_nonnegative_int(mal_id)
        if not safe_id:
            raise MalIntegrationError("Geçersiz MyAnimeList manga kimliği.")
        allowed_statuses = {"reading", "completed", "on_hold", "dropped", "plan_to_read"}
        status = str(payload.get("status") or "")
        if status not in allowed_statuses:
            raise MalIntegrationError("Geçersiz MyAnimeList okuma durumu.")
        body = {
            "status": status,
            "score": min(10, self._safe_nonnegative_int(payload.get("score"))),
            "num_chapters_read": self._safe_nonnegative_int(payload.get("num_chapters_read")),
            "num_volumes_read": self._safe_nonnegative_int(payload.get("num_volumes_read")),
        }
        response = self._authorized_request(
            "PATCH",
            f"{MAL_API_URL}/manga/{safe_id}/my_list_status",
            data=body,
        )
        return self._normalized_list_status(response)

    def fetch_preview(
        self,
        *,
        force: bool = False,
        progress_callback: Callable[[str, int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        def progress(stage: str, processed: int, total: int) -> None:
            if cancel_event is not None and cancel_event.is_set():
                from mangax.integrations.mal_sync import MalSyncCancelled

                raise MalSyncCancelled("MyAnimeList senkronizasyonu iptal edildi.")
            if progress_callback:
                progress_callback(stage, processed, total)

        with self._lock:
            if not force and self._preview["entries"] and time.time() - self._preview["saved_at"] < 600:
                entries = list(self._preview["entries"])
                return self._preview_payload(entries, cached=True)
        progress("fetching", 0, 0)
        fields = "list_status,alternative_titles,start_date,media_type,status,num_chapters,num_volumes"
        next_url = f"{MAL_API_URL}/users/@me/mangalist?{urlencode({'limit': 100, 'fields': fields, 'sort': 'list_updated_at'})}"
        raw_entries: list[dict[str, Any]] = []
        visited_urls: set[str] = set()
        for _ in range(MAX_MAL_LIST_PAGES):
            if next_url in visited_urls:
                raise MalIntegrationError("MAL sayfalama adresi tekrarlandı.")
            visited_urls.add(next_url)
            response = self._authorized_get(next_url)
            raw_entries.extend(item for item in response.get("data") or [] if isinstance(item, dict))
            progress("fetching", len(raw_entries), 0)
            candidate = str((response.get("paging") or {}).get("next") or "")
            if not candidate:
                break
            if not candidate.startswith(f"{MAL_API_URL}/users/"):
                raise MalIntegrationError("MAL sayfalama adresi güvenlik kontrolünden geçmedi.")
            next_url = candidate
        else:
            raise MalIntegrationError("MAL manga listesi güvenli sayfalama sınırını aştı.")
        mal_ids = [
            self._safe_nonnegative_int(self._mapping(item.get("node")).get("id"))
            for item in raw_entries
        ]
        valid_ids = [value for value in mal_ids if value > 0]
        progress("matching", 0, len(valid_ids))
        with self._lock:
            matcher = self._manga_matcher
        if matcher:
            try:
                matched_value = matcher(valid_ids)
                matches = matched_value if isinstance(matched_value, dict) else {}
            except Exception:
                # Full edition'daki AniList zenginleştirmesi ikincil bir ağ servisidir.
                # Bu servis bozulduğunda MAL listesindeki güvenilir kimlikler kaybolmamalı;
                # kayıtlar aşağıda kararlı mal_<id> kimliğiyle içe alınabilir.
                matches = {}
        else:
            matches = {
                self._safe_nonnegative_int(self._mapping(item.get("node")).get("id")): self._reader_manga(
                    self._mapping(item.get("node")),
                    self._safe_nonnegative_int(self._mapping(item.get("node")).get("id")),
                )
                for item in raw_entries
                if self._safe_nonnegative_int(self._mapping(item.get("node")).get("id")) > 0
            }
        progress("matching", len(valid_ids), len(valid_ids))
        entries = []
        for item in raw_entries:
            node = self._mapping(item.get("node"))
            list_status = self._mapping(item.get("list_status"))
            mal_id = self._safe_nonnegative_int(node.get("id"))
            exact_value = matches.get(mal_id)
            exact_match = exact_value if isinstance(exact_value, dict) else None
            fallback_match = (
                self._reader_manga(node, mal_id)
                if matcher and mal_id > 0 and exact_match is None
                else None
            )
            match = exact_match or fallback_match
            picture = node.get("main_picture") if isinstance(node.get("main_picture"), dict) else {}
            entries.append({
                "mal_id": mal_id,
                "title": str(node.get("title") or "Bilinmeyen Manga"),
                "cover_url": str(picture.get("large") or picture.get("medium") or ""),
                "status": str(list_status.get("status") or "plan_to_read"),
                "score": min(10, self._safe_nonnegative_int(list_status.get("score"))),
                "num_chapters_read": self._safe_nonnegative_int(list_status.get("num_chapters_read")),
                "num_volumes_read": self._safe_nonnegative_int(list_status.get("num_volumes_read")),
                "remote_updated_at": str(list_status.get("updated_at") or "")[:80],
                # `matched` gelişmiş/seçmeli arayüzde kesin AniList eşleşmesini anlatır.
                # Otomatik senkron `fallback` kaydını da güvenli MAL kimliğiyle aktarır.
                "matched": bool(exact_match),
                "fallback": bool(fallback_match),
                "manga": match,
            })
        with self._lock:
            self._preview = {"saved_at": time.time(), "entries": entries}
        return self._preview_payload(entries, cached=False)

    @staticmethod
    def _safe_nonnegative_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _preview_payload(entries: list[dict[str, Any]], *, cached: bool) -> dict[str, Any]:
        return {
            "entries": entries,
            "total": len(entries),
            "matched": sum(bool(item.get("matched")) for item in entries),
            "unmatched": sum(not item.get("matched") for item in entries),
            "cached": cached,
            "read_only": True,
        }

    def selected_preview_entries(self, mal_ids: list[int]) -> list[dict[str, Any]]:
        selected = {max(0, int(value)) for value in mal_ids}
        with self._lock:
            entries = list(self._preview.get("entries") or [])
        if not entries:
            raise MalIntegrationError("Önce MAL listesini önizleyin.")
        return [item for item in entries if item.get("matched") and item.get("mal_id") in selected]

    def disconnect(self) -> dict[str, Any]:
        credentials = self._load_credentials()
        secret = credentials.get("client_secret")
        self._save_credentials({"client_secret": secret} if secret else {})
        with self._lock:
            self._pending.clear()
            self._preview = {"saved_at": 0.0, "entries": []}
        with self._lock:
            config = self._load_config()
            config.pop("last_sync", None)
            config.pop("last_success", None)
            config.pop("last_error", None)
            config["two_way_sync"] = False
            self._save_config(config)
        return self.status()


mal_integration_manager = MalIntegrationManager()
