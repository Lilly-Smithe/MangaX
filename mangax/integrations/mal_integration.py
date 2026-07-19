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

from mangax.core.config import APP_URL, DATA_DIR
from mangax.integrations.secure_store import SecureStoreError, WindowsDpapiJsonStore


MAL_AUTHORIZE_URL = "https://myanimelist.net/v1/oauth2/authorize"
MAL_TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"
MAL_API_URL = "https://api.myanimelist.net/v2"
MAL_CALLBACK_URL = f"{APP_URL}/api/integrations/mal/callback"
MAL_CONFIG_PATH = Path(DATA_DIR) / "mal_integration.json"
MAL_CREDENTIALS_PATH = Path(DATA_DIR) / ".mal_credentials"
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,160}$")


class MalIntegrationError(RuntimeError):
    pass


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
        return str(os.getenv("MANGAX_MAL_CLIENT_ID") or self._load_config().get("client_id") or "").strip()

    def configure(self, client_id: str, client_secret: str = "") -> dict[str, Any]:
        normalized = str(client_id or "").strip()
        if not CLIENT_ID_PATTERN.fullmatch(normalized):
            raise MalIntegrationError("Geçerli bir MyAnimeList Client ID girilmelidir.")
        self._save_config({"client_id": normalized})
        if client_secret:
            credentials = self._load_credentials()
            credentials["client_secret"] = str(client_secret).strip()
            self._save_credentials(credentials)
        return self.status()

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
        return {
            "configured": bool(client_id),
            "client_id": client_id,
            "connected": bool(credentials.get("access_token")),
            "username": str(profile.get("name") or ""),
            "callback_url": MAL_CALLBACK_URL,
            "secure_storage": storage_available,
            "storage_error": storage_error,
        }

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

    def _authorized_get(self, url: str) -> dict[str, Any]:
        credentials = self._load_credentials()
        if not credentials.get("access_token"):
            raise MalIntegrationError("MyAnimeList hesabı bağlı değil.")
        if int(credentials.get("expires_at") or 0) <= int(time.time()) + 60:
            credentials = self._refresh_access_token(credentials)
        try:
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {credentials['access_token']}"},
                timeout=25.0,
                follow_redirects=True,
            )
            if response.status_code == 401:
                credentials = self._refresh_access_token(credentials)
                response = httpx.get(
                    url,
                    headers={"Authorization": f"Bearer {credentials['access_token']}"},
                    timeout=25.0,
                    follow_redirects=True,
                )
            response.raise_for_status()
            value = response.json()
            return value if isinstance(value, dict) else {}
        except MalIntegrationError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise MalIntegrationError("MyAnimeList listesine şu anda ulaşılamıyor.") from error

    def fetch_preview(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if not force and self._preview["entries"] and time.time() - self._preview["saved_at"] < 600:
                entries = list(self._preview["entries"])
                return self._preview_payload(entries, cached=True)
        fields = "list_status,alternative_titles,start_date,media_type,status,num_chapters,num_volumes"
        next_url = f"{MAL_API_URL}/users/@me/mangalist?{urlencode({'limit': 100, 'fields': fields, 'sort': 'list_updated_at'})}"
        raw_entries: list[dict[str, Any]] = []
        for _ in range(20):
            response = self._authorized_get(next_url)
            raw_entries.extend(item for item in response.get("data") or [] if isinstance(item, dict))
            candidate = str((response.get("paging") or {}).get("next") or "")
            if not candidate:
                break
            if not candidate.startswith(f"{MAL_API_URL}/users/"):
                raise MalIntegrationError("MAL sayfalama adresi güvenlik kontrolünden geçmedi.")
            next_url = candidate
        mal_ids = [int((item.get("node") or {}).get("id") or 0) for item in raw_entries]
        valid_ids = [value for value in mal_ids if value > 0]
        with self._lock:
            matcher = self._manga_matcher
        matches = matcher(valid_ids) if matcher else {
            int((item.get("node") or {}).get("id") or 0): self._reader_manga(
                item.get("node") or {}, int((item.get("node") or {}).get("id") or 0)
            )
            for item in raw_entries
            if int((item.get("node") or {}).get("id") or 0) > 0
        }
        entries = []
        for item in raw_entries:
            node = item.get("node") or {}
            list_status = item.get("list_status") or {}
            mal_id = int(node.get("id") or 0)
            match = matches.get(mal_id)
            entries.append({
                "mal_id": mal_id,
                "title": str(node.get("title") or "Bilinmeyen Manga"),
                "cover_url": str((node.get("main_picture") or {}).get("medium") or ""),
                "status": str(list_status.get("status") or "plan_to_read"),
                "score": max(0, min(10, int(list_status.get("score") or 0))),
                "num_chapters_read": max(0, int(list_status.get("num_chapters_read") or 0)),
                "num_volumes_read": max(0, int(list_status.get("num_volumes_read") or 0)),
                "matched": bool(match),
                "manga": match,
            })
        with self._lock:
            self._preview = {"saved_at": time.time(), "entries": entries}
        return self._preview_payload(entries, cached=False)

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
        return self.status()


mal_integration_manager = MalIntegrationManager()
