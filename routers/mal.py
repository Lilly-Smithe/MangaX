from __future__ import annotations

import html
import threading
import webbrowser
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from mangax.core.dependencies import library_manager
from mangax.integrations.mal_integration import MAL_CALLBACK_URL, MalIntegrationError, mal_integration_manager
from mangax.integrations.mal_sync_jobs import MalSyncJobError, mal_sync_job_manager
from mangax.integrations.mal_outbound import mal_outbound_service


router = APIRouter(prefix="/api/integrations/mal", tags=["MyAnimeList"])


class MalConfigureRequest(BaseModel):
    client_id: str = Field(min_length=8, max_length=160)
    client_secret: str = Field(default="", max_length=300)


class MalImportRequest(BaseModel):
    mal_ids: list[int] = Field(min_length=1, max_length=1000)


class MalSyncPreferencesRequest(BaseModel):
    automatic_sync: bool
    sync_interval: str = Field(default="24h", pattern=r"^(startup|6h|12h|24h)$")
    two_way_sync: bool | None = None


class MalConflictResolutionRequest(BaseModel):
    choice: str = Field(pattern=r"^(remote|local)$")


def _http_error(error: MalIntegrationError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@router.get("/status")
def mal_status() -> dict[str, Any]:
    return mal_integration_manager.status()


@router.put("/configure")
def configure_mal(request: MalConfigureRequest) -> dict[str, Any]:
    try:
        return mal_integration_manager.configure(request.client_id, request.client_secret)
    except MalIntegrationError as error:
        raise _http_error(error) from error


@router.put("/sync/preferences")
def configure_mal_sync_preferences(request: MalSyncPreferencesRequest) -> dict[str, Any]:
    try:
        status = mal_integration_manager.set_sync_preferences(
            request.automatic_sync,
            request.sync_interval,
            request.two_way_sync,
        )
        from mangax.integrations.mal_sync_scheduler import mal_sync_scheduler

        mal_sync_scheduler.preferences_changed()
        return status
    except MalIntegrationError as error:
        raise _http_error(error) from error


@router.post("/connect")
def connect_mal() -> dict[str, Any]:
    try:
        authorization_url = mal_integration_manager.start_oauth()
    except MalIntegrationError as error:
        raise _http_error(error) from error
    threading.Thread(target=webbrowser.open, args=(authorization_url,), daemon=True).start()
    return {"status": "authorization_required", "authorization_url": authorization_url, "callback_url": MAL_CALLBACK_URL}


@router.get("/callback", response_class=HTMLResponse)
def mal_callback(
    state: str = Query(default="", max_length=200),
    code: str = Query(default="", max_length=1000),
    error: str = Query(default="", max_length=200),
) -> HTMLResponse:
    if error:
        message = "MyAnimeList bağlantı izni verilmedi. Bu pencereyi kapatıp MangaX'e dönebilirsiniz."
        success = False
    else:
        try:
            result = mal_integration_manager.complete_oauth(state, code)
            automatic_sync = mal_integration_manager.automatic_sync_enabled()
            if automatic_sync:
                try:
                    mal_sync_job_manager.start(trigger="oauth")
                except (MalSyncJobError, MalIntegrationError):
                    # OAuth bağlantısı başarılı kalır; kullanıcı işi MangaX içinden
                    # yeniden başlatabilir. Callback senkronizasyonu beklemez.
                    pass
            message = (
                f"{result.get('username') or 'MyAnimeList'} hesabı MangaX'e bağlandı. "
                + (
                    "Kütüphane eşitlemesi arka planda başladı; bu pencereyi kapatabilirsiniz."
                    if automatic_sync
                    else "Bu pencereyi kapatıp eşitlemeyi MangaX içinden başlatabilirsiniz."
                )
            )
            success = True
        except MalIntegrationError as integration_error:
            message = str(integration_error)
            success = False
    color = "#36d399" if success else "#ff5962"
    icon = "✓" if success else "!"
    body = f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>MangaX · MyAnimeList</title>
    <meta name="viewport" content="width=device-width,initial-scale=1"><style>
    body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#07080c;color:#fff;font-family:Segoe UI,sans-serif}}
    main{{width:min(88vw,460px);padding:34px;border:1px solid #272832;border-radius:18px;background:#111218;text-align:center;box-shadow:0 24px 70px #0008}}
    i{{width:58px;height:58px;display:grid;place-items:center;margin:0 auto 18px;border-radius:50%;background:{color}22;color:{color};font-style:normal;font-size:30px;font-weight:800}}
    h1{{font-size:22px;margin:0 0 10px}}p{{color:#b8bac5;line-height:1.6;margin:0}}b{{color:#e50914}}
    </style></head><body><main><i>{icon}</i><h1><b>MangaX</b> · MyAnimeList</h1><p>{html.escape(message)}</p></main></body></html>"""
    return HTMLResponse(body, status_code=200 if success else 422)


@router.get("/preview")
def preview_mal_import(force: bool = False) -> dict[str, Any]:
    try:
        return mal_integration_manager.fetch_preview(force=force)
    except MalIntegrationError as error:
        raise _http_error(error) from error


@router.post("/import")
def import_mal_library(request: MalImportRequest) -> dict[str, Any]:
    try:
        entries = mal_integration_manager.selected_preview_entries(request.mal_ids)
    except MalIntegrationError as error:
        raise _http_error(error) from error
    imported = []
    failed = []
    for entry in entries:
        try:
            imported.append(library_manager.import_mal_entry(entry["manga"], entry))
        except (ValueError, TypeError, OSError) as error:
            failed.append({"mal_id": entry.get("mal_id"), "title": entry.get("title"), "error": str(error)})
    return {
        "imported": len(imported),
        "failed": failed,
        "requested": len(request.mal_ids),
        "read_only": True,
        "message": f"{len(imported)} manga MyAnimeList'ten içe aktarıldı.",
    }


@router.post("/sync")
def sync_mal_library() -> dict[str, Any]:
    try:
        return mal_sync_job_manager.start(trigger="manual")
    except MalSyncJobError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/sync/status")
def mal_sync_status() -> dict[str, Any]:
    return mal_sync_job_manager.status()


@router.get("/sync/summary")
def mal_sync_summary() -> dict[str, Any]:
    return mal_sync_job_manager.last_summary()


@router.get("/outbound/status")
def mal_outbound_status() -> dict[str, Any]:
    return mal_outbound_service.status()


@router.post("/outbound/retry")
def retry_mal_outbound() -> dict[str, Any]:
    return mal_outbound_service.retry_all()


@router.post("/outbound/conflicts/{manga_id}/resolve")
def resolve_mal_conflict(
    manga_id: str,
    request: MalConflictResolutionRequest,
) -> dict[str, Any]:
    try:
        return mal_outbound_service.resolve_conflict(manga_id, request.choice)
    except MalIntegrationError as error:
        raise _http_error(error) from error


@router.delete("/sync")
def cancel_mal_sync() -> dict[str, Any]:
    return mal_sync_job_manager.cancel()


@router.delete("/disconnect")
def disconnect_mal() -> dict[str, Any]:
    try:
        mal_sync_job_manager.cancel()
        return mal_integration_manager.disconnect()
    except MalIntegrationError as error:
        raise _http_error(error) from error
