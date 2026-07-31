"""GitHub App cihaz akışı ve private eklenti deposu yetkilendirmesi."""

from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from mangax.core.config import DATA_DIR, GITHUB_ACCESS_REPOSITORY, GITHUB_OAUTH_CLIENT_ID
from mangax.integrations.secure_store import SecureStoreError, WindowsDpapiJsonStore


GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"
GITHUB_CREDENTIALS_PATH = Path(DATA_DIR) / ".github_credentials"


class GitHubIntegrationError(RuntimeError):
    code = "github_error"
    status_code = 502


class GitHubConfigurationError(GitHubIntegrationError):
    code = "configuration_error"
    status_code = 503


class GitHubAccessDeniedError(GitHubIntegrationError):
    code = "access_not_found"
    status_code = 403


class GitHubFlowCancelledError(GitHubIntegrationError):
    code = "connection_cancelled"
    status_code = 409


class GitHubFlowExpiredError(GitHubIntegrationError):
    code = "connection_expired"
    status_code = 408


class GitHubNotConnectedError(GitHubIntegrationError):
    code = "not_connected"
    status_code = 401


class GitHubSessionExpiredError(GitHubIntegrationError):
    code = "session_expired"
    status_code = 401


class GitHubSecureStoreError(GitHubIntegrationError):
    code = "secure_storage_error"
    status_code = 500


class GitHubIntegrationManager:
    def __init__(
        self,
        *,
        client_id: str = GITHUB_OAUTH_CLIENT_ID,
        repository: str = GITHUB_ACCESS_REPOSITORY,
        credential_store: WindowsDpapiJsonStore | None = None,
    ):
        self.client_id = str(client_id or "").strip()
        self.repository = str(repository or "").strip()
        self.credential_store = credential_store or WindowsDpapiJsonStore(
            GITHUB_CREDENTIALS_PATH, "MangaX GitHub hesabı"
        )
        self._lock = threading.RLock()
        self._verification_lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._verified_at = 0.0

    def _load_credentials(self) -> dict[str, Any]:
        try:
            return self.credential_store.load()
        except SecureStoreError as error:
            raise GitHubSecureStoreError(str(error)) from error

    def _save_credentials(self, value: dict[str, Any]) -> None:
        try:
            self.credential_store.save(value)
        except SecureStoreError as error:
            raise GitHubSecureStoreError(str(error)) from error

    @staticmethod
    def _api_headers(token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "MangaX-GitHub-Integration",
        }

    def _verify_token(self, token: str) -> dict[str, Any]:
        try:
            profile_response = httpx.get(
                f"{GITHUB_API_URL}/user",
                headers=self._api_headers(token),
                timeout=15.0,
                follow_redirects=False,
            )
            if profile_response.status_code in {401, 403}:
                raise GitHubSessionExpiredError("GitHub oturumunun süresi doldu. Hesabı yeniden bağlayın.")
            profile_response.raise_for_status()
            profile = profile_response.json()
            repository_response = httpx.get(
                f"{GITHUB_API_URL}/repos/{self.repository}",
                headers=self._api_headers(token),
                timeout=15.0,
                follow_redirects=False,
            )
            if repository_response.status_code in {401, 403, 404}:
                raise GitHubAccessDeniedError("Bu hesap için erişim bulunamadı")
            repository_response.raise_for_status()
            repository = repository_response.json()
        except (GitHubAccessDeniedError, GitHubSessionExpiredError):
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise GitHubIntegrationError(
                "GitHub doğrulama hizmetine ulaşılamadı. Lütfen daha sonra yeniden deneyin."
            ) from error
        if not isinstance(profile, dict) or not profile.get("login"):
            raise GitHubIntegrationError("GitHub geçerli bir kullanıcı profili göndermedi.")
        permissions = repository.get("permissions") if isinstance(repository, dict) else {}
        if isinstance(permissions, dict) and not permissions.get("pull", False):
            raise GitHubAccessDeniedError("Bu hesap için erişim bulunamadı")
        return {
            "id": profile.get("id"),
            "login": str(profile.get("login") or ""),
            "avatar_url": str(profile.get("avatar_url") or ""),
            "repository": str(repository.get("full_name") or self.repository),
        }

    def _clear_expired_pending(self) -> None:
        now = time.time()
        self._pending = {
            key: value for key, value in self._pending.items()
            if now < float(value.get("expires_at") or 0)
        }

    def start_device_flow(self) -> dict[str, Any]:
        if not self.client_id:
            raise GitHubConfigurationError(
                "MangaX GitHub bağlantısı henüz yapılandırılmadı. GitHub App Client ID eksik."
            )
        try:
            response = httpx.post(
                GITHUB_DEVICE_CODE_URL,
                data={"client_id": self.client_id, "scope": "repo"},
                headers={"Accept": "application/json", "User-Agent": "MangaX-GitHub-Integration"},
                timeout=15.0,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise GitHubIntegrationError("GitHub bağlantısı başlatılamadı.") from error
        device_code = str(payload.get("device_code") or "")
        user_code = str(payload.get("user_code") or "")
        verification_uri = str(payload.get("verification_uri") or "")
        if not device_code or not user_code or not verification_uri.startswith("https://github.com/"):
            raise GitHubIntegrationError("GitHub geçerli bir cihaz bağlantı kodu göndermedi.")
        expires_in = max(60, min(1800, int(payload.get("expires_in") or 900)))
        interval = max(5, min(30, int(payload.get("interval") or 5)))
        request_id = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            self._clear_expired_pending()
            self._pending[request_id] = {
                "device_code": device_code,
                "expires_at": now + expires_in,
                "interval": interval,
                "next_poll_at": now,
            }
        return {
            "status": "authorization_required",
            "request_id": request_id,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "expires_in": expires_in,
            "interval": interval,
        }

    def poll_device_flow(self, request_id: str) -> dict[str, Any]:
        normalized = str(request_id or "").strip()
        with self._lock:
            pending = self._pending.get(normalized)
            if not pending:
                raise GitHubFlowExpiredError("GitHub bağlantı isteğinin süresi doldu. Yeniden deneyin.")
            now = time.time()
            if now >= float(pending.get("expires_at") or 0):
                self._pending.pop(normalized, None)
                raise GitHubFlowExpiredError("GitHub bağlantı isteğinin süresi doldu. Yeniden deneyin.")
            if now < float(pending.get("next_poll_at") or 0):
                return {
                    "status": "pending",
                    "retry_after": max(1, int(float(pending["next_poll_at"]) - now) + 1),
                }
            pending["next_poll_at"] = now + int(pending["interval"])
        try:
            response = httpx.post(
                GITHUB_ACCESS_TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "device_code": pending["device_code"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json", "User-Agent": "MangaX-GitHub-Integration"},
                timeout=15.0,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise GitHubIntegrationError("GitHub bağlantı sonucu alınamadı.") from error
        error_code = str(payload.get("error") or "")
        if error_code == "authorization_pending":
            return {"status": "pending", "retry_after": int(pending["interval"])}
        if error_code == "slow_down":
            with self._lock:
                pending["interval"] = min(30, int(pending["interval"]) + 5)
            return {"status": "pending", "retry_after": int(pending["interval"])}
        if error_code:
            with self._lock:
                self._pending.pop(normalized, None)
            if error_code == "access_denied":
                raise GitHubFlowCancelledError("GitHub bağlantısı iptal edildi.")
            if error_code == "expired_token":
                raise GitHubFlowExpiredError("GitHub bağlantı kodunun süresi doldu.")
            raise GitHubIntegrationError("GitHub bağlantısı tamamlanamadı.")
        token = str(payload.get("access_token") or "")
        if not token:
            raise GitHubIntegrationError("GitHub geçerli bir erişim anahtarı göndermedi.")
        try:
            profile = self._verify_token(token)
        except GitHubIntegrationError:
            with self._lock:
                self._pending.pop(normalized, None)
            raise
        expires_in = max(0, int(payload.get("expires_in") or 0))
        credentials = {
            "access_token": token,
            "token_type": str(payload.get("token_type") or "bearer"),
            "scope": str(payload.get("scope") or ""),
            "expires_at": int(time.time()) + expires_in if expires_in else 0,
            "profile": profile,
            "repository": self.repository,
            "connected_at": int(time.time()),
        }
        try:
            self._save_credentials(credentials)
        except GitHubIntegrationError:
            with self._lock:
                self._pending.pop(normalized, None)
            raise
        with self._lock:
            self._pending.pop(normalized, None)
            self._verified_at = time.time()
        return self.status(validate=False)

    def cancel_device_flow(self, request_id: str) -> dict[str, Any]:
        normalized = str(request_id or "").strip()
        with self._lock:
            cancelled = self._pending.pop(normalized, None) is not None
        return {"status": "cancelled", "cancelled": cancelled}

    def _authorized_credentials(self, *, validate: bool = True) -> dict[str, Any]:
        credentials = self._load_credentials()
        token = str(credentials.get("access_token") or "")
        if not token:
            raise GitHubNotConnectedError("GitHub hesabı bağlı değil.")
        expires_at = int(credentials.get("expires_at") or 0)
        if expires_at and expires_at <= int(time.time()) + 30:
            raise GitHubSessionExpiredError("GitHub oturumunun süresi doldu. Hesabı yeniden bağlayın.")
        if str(credentials.get("repository") or "") != self.repository:
            raise GitHubNotConnectedError("GitHub hesabını yeniden bağlayın.")
        if validate and time.time() - self._verified_at > 60:
            # GitHub durum denetimi ile otomatik guncelleme ayni anda baslayabilir.
            # Tek kilit, ayni token icin iki ayri /user + /repos turu acilmasini engeller.
            with self._verification_lock:
                if time.time() - self._verified_at > 60:
                    profile = self._verify_token(token)
                    credentials["profile"] = profile
                    self._save_credentials(credentials)
                    self._verified_at = time.time()
        return credentials

    def require_access(self, *, validate: bool = True) -> str:
        return str(self._authorized_credentials(validate=validate)["access_token"])

    def status(self, *, validate: bool = True) -> dict[str, Any]:
        try:
            credentials = self._authorized_credentials(validate=validate)
            profile = credentials.get("profile") if isinstance(credentials.get("profile"), dict) else {}
            return {
                "configured": bool(self.client_id),
                "connected": True,
                "authorized": True,
                "extensions_available": True,
                "username": str(profile.get("login") or ""),
                "avatar_url": str(profile.get("avatar_url") or ""),
                "repository": self.repository,
                "error": "",
                "error_code": "",
                "secure_storage": True,
            }
        except GitHubIntegrationError as error:
            try:
                credentials = self._load_credentials()
                connected = bool(credentials.get("access_token"))
            except GitHubIntegrationError:
                connected = False
            message = str(error)
            if not connected and message == "GitHub hesabı bağlı değil.":
                message = ""
            return {
                "configured": bool(self.client_id),
                "connected": connected,
                "authorized": False,
                "extensions_available": False,
                "username": "",
                "avatar_url": "",
                "repository": self.repository,
                "error": message,
                "error_code": getattr(error, "code", "github_error"),
                "secure_storage": "Güvenli hesap verisi" not in str(error),
            }

    def disconnect(self) -> dict[str, Any]:
        try:
            self.credential_store.clear()
        except SecureStoreError as error:
            raise GitHubSecureStoreError(str(error)) from error
        with self._lock:
            self._pending.clear()
            self._verified_at = 0.0
        return self.status(validate=False)


github_integration_manager = GitHubIntegrationManager()
