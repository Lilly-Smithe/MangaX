# routers/frontend.py
# Ana sayfa

import os
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from mangax.core.config import STATIC_DIR

router = APIRouter(tags=["Frontend"])


@router.get("/")
def serve_index():
    """Ana sayfa"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        return JSONResponse(
            {"status": "error", "message": "Frontend henüz yok."},
            status_code=503
        )
    return FileResponse(index_path)