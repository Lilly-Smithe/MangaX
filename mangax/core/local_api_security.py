"""MangaX'in localhost API'si icin surec-ici guvenlik siniri.

Oturum anahtari her Python sureci icin yeniden uretilir. Anahtar yalnizca
HttpOnly, kalici olmayan bir localhost oturum cerezinde tasinir; URL'ye,
HTML'e, loglara veya uygulama veri dosyalarina yazilmaz.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


SESSION_COOKIE_NAME = "mangax_local_session"
SESSION_HEADER_NAME = "X-MangaX-Session"
_PROTECTED_PREFIXES = ("/api", "/downloads", "/local-manga")
_OAUTH_CALLBACK_PATHS = {"/api/integrations/mal/callback"}


@dataclass(frozen=True)
class LocalApiSecurityConfig:
    host: str
    port: int
    session_token: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)

    @property
    def allowed_hostnames(self) -> frozenset[str]:
        return frozenset({self.host.lower(), "127.0.0.1", "localhost", "::1"})

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return (
            f"http://127.0.0.1:{self.port}",
            f"http://localhost:{self.port}",
            f"http://[::1]:{self.port}",
        )


def _parse_host_header(value: str) -> tuple[str, int | None] | None:
    raw = (value or "").strip()
    if not raw or any(character in raw for character in "\r\n/@"):
        return None
    try:
        parsed = urlsplit(f"//{raw}")
        if parsed.username or parsed.password or not parsed.hostname:
            return None
        return parsed.hostname.lower().rstrip("."), parsed.port
    except ValueError:
        return None


def _is_allowed_origin(origin: str, config: LocalApiSecurityConfig) -> bool:
    return origin in config.allowed_origins


class LocalApiSecurityMiddleware(BaseHTTPMiddleware):
    """Host/Origin denetimi ve localhost oturum dogrulamasi uygular."""

    def __init__(self, app, *, config: LocalApiSecurityConfig):
        super().__init__(app)
        self.config = config

    async def dispatch(self, request: Request, call_next) -> Response:
        host = _parse_host_header(request.headers.get("host", ""))
        if (
            host is None
            or host[0] not in self.config.allowed_hostnames
            or host[1] not in {None, self.config.port}
        ):
            return JSONResponse({"detail": "Gecersiz yerel API hedefi."}, status_code=400)

        origin = request.headers.get("origin")
        if origin and not _is_allowed_origin(origin, self.config):
            return JSONResponse({"detail": "Bu origin icin erisim reddedildi."}, status_code=403)

        path = request.url.path
        is_preflight = request.method == "OPTIONS"
        is_oauth_callback = request.method == "GET" and path in _OAUTH_CALLBACK_PATHS
        is_protected = path.startswith(_PROTECTED_PREFIXES)

        if is_protected and not is_preflight and not is_oauth_callback:
            supplied = request.headers.get(SESSION_HEADER_NAME) or request.cookies.get(SESSION_COOKIE_NAME, "")
            if not supplied or not secrets.compare_digest(supplied, self.config.session_token):
                return JSONResponse({"detail": "Yerel uygulama oturumu gerekli."}, status_code=401)

        response = await call_next(request)
        if request.method == "GET" and path == "/" and response.status_code < 400:
            response.set_cookie(
                SESSION_COOKIE_NAME,
                self.config.session_token,
                httponly=True,
                secure=False,
                samesite="strict",
                path="/",
            )
            response.headers["Cache-Control"] = "no-store"
        return response


def configure_local_api_security(app: FastAPI, *, host: str, port: int) -> LocalApiSecurityConfig:
    """FastAPI uygulamasina tek bir MangaX localhost siniri kurar."""

    config = LocalApiSecurityConfig(host=host, port=int(port))
    app.state.local_api_security = config
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", SESSION_HEADER_NAME],
        max_age=600,
    )
    app.add_middleware(LocalApiSecurityMiddleware, config=config)
    return config
