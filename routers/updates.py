"""Uygulama guncelleme kontrolu, indirme ve kurulum endpointleri."""

from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from mangax.core.config import APP_EDITION, APP_VERSION
from mangax.core.preferences_manager import preferences_manager
from mangax.integrations.app_update import AppUpdateError, app_update_manager
from mangax.integrations.github_integration import GitHubIntegrationError


router = APIRouter(prefix="/api/updates", tags=["Updates"])


class DownloadRequest(BaseModel):
    update_id: str
    confirmed: bool = False


class InstallRequest(BaseModel):
    confirmed: bool = False


class SkipRequest(BaseModel):
    version: str


def _error(error: Exception) -> HTTPException:
    return HTTPException(status_code=getattr(error, "status_code", 502), detail=str(error), headers={"X-MangaX-Update-Error": getattr(error, "code", "update_error")})


@router.get("/check")
def check_update(startup: bool = Query(default=False)) -> dict[str, Any]:
    if startup and not preferences_manager.get_all().get("automatic_update_checks", True):
        return {"skipped": True, "current_version": APP_VERSION, "edition": APP_EDITION, "update_available": False}
    try:
        result = app_update_manager.check()
        preferences_manager.update({"last_app_update_check": result.get("checked_at", "")})
        skipped = str(preferences_manager.get_all().get("skipped_app_update_version") or "")
        if startup and result.get("latest_version") == skipped:
            result.update(update_available=False, skipped_version=True, update_id="")
        return result
    except (AppUpdateError, GitHubIntegrationError) as error:
        raise _error(error) from error


@router.post("/download")
def start_download(request: DownloadRequest) -> dict[str, Any]:
    try:
        return app_update_manager.start_download(request.update_id, confirmed=request.confirmed)
    except (AppUpdateError, GitHubIntegrationError) as error:
        raise _error(error) from error


@router.get("/download/{job_id}")
def download_status(job_id: str = Path(min_length=16, max_length=200)) -> dict[str, Any]:
    try:
        return app_update_manager.status(job_id)
    except AppUpdateError as error:
        raise _error(error) from error


@router.delete("/download/{job_id}")
def cancel_download(job_id: str = Path(min_length=16, max_length=200)) -> dict[str, Any]:
    try:
        return app_update_manager.cancel(job_id)
    except AppUpdateError as error:
        raise _error(error) from error


@router.post("/download/{job_id}/resume")
def resume_download(job_id: str = Path(min_length=16, max_length=200)) -> dict[str, Any]:
    try:
        return app_update_manager.resume(job_id)
    except AppUpdateError as error:
        raise _error(error) from error


@router.post("/download/{job_id}/install")
def install_update(request: InstallRequest, job_id: str = Path(min_length=16, max_length=200)) -> dict[str, Any]:
    try:
        return app_update_manager.install(job_id, confirmed=request.confirmed)
    except AppUpdateError as error:
        raise _error(error) from error


@router.post("/skip")
def skip_update(request: SkipRequest) -> dict[str, Any]:
    from mangax.integrations.app_update import version_tuple
    try:
        version_tuple(request.version)
    except AppUpdateError as error:
        raise _error(error) from error
    preferences_manager.update({"skipped_app_update_version": request.version})
    return {"status": "skipped", "version": request.version}


@router.get("/result")
def update_result(consume: bool = Query(default=False)) -> dict[str, Any]:
    return app_update_manager.last_result(consume=consume)
