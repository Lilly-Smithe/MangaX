# routers/frontend.py
# Ana sayfa

import os
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from mangax.core.config import STATIC_DIR

router = APIRouter(tags=["Frontend"])

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
    "img-src 'self' http: https: data: blob:; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
)


@router.get("/")
def serve_index():
    """Ana sayfa"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        return JSONResponse(
            {"status": "error", "message": "Frontend henüz yok."},
            status_code=503
        )
    return FileResponse(
        index_path,
        headers={
            "Content-Security-Policy": CONTENT_SECURITY_POLICY,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
