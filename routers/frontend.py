# routers/frontend.py
# Ana sayfa

import os
import base64
import hashlib
import re
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from mangax.core.config import STATIC_DIR
from mangax.core.preferences_manager import preferences_manager

router = APIRouter(tags=["Frontend"])

CONTENT_SECURITY_POLICY_BASE = (
    "default-src 'self'; "
    "{script_policy}; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
    "img-src 'self' http: https: data: blob:; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
)


def _content_security_policy(html: str) -> str:
    """Authorize only the exact legacy inline handlers shipped by MangaX."""
    handlers = set(re.findall(r"\bon(?:click|change|input|submit|keydown|error)=\"([^\"]+)\"", html))
    hashes = []
    for handler in sorted(handlers):
        digest = hashlib.sha256(handler.encode("utf-8")).digest()
        hashes.append("'sha256-" + base64.b64encode(digest).decode("ascii") + "'")
    script_policy = "script-src 'self'"
    if hashes:
        script_policy += " 'unsafe-hashes' " + " ".join(hashes)
    return CONTENT_SECURITY_POLICY_BASE.format(script_policy=script_policy)


@router.get("/")
def serve_index():
    """Ana sayfa"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        return JSONResponse(
            {"status": "error", "message": "Frontend henüz yok."},
            status_code=503
        )
    theme = str(preferences_manager.get_all().get("app_theme") or "dark")
    if theme not in {"dark", "light", "cover_grid", "windows_xp", "pornhub"}:
        theme = "dark"
    try:
        with open(index_path, "r", encoding="utf-8") as index_file:
            html = index_file.read()
    except OSError:
        return JSONResponse(
            {"status": "error", "message": "Frontend okunamadı."},
            status_code=503,
        )
    html = html.replace('<html lang="tr">', f'<html lang="tr" data-theme="{theme}">', 1)
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": _content_security_policy(html),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )
