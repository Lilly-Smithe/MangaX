"""Gizli Reader panelinin doğrulanmış MangaX Full kurulum uçları."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from full_release import FullReleaseError, full_release_manager
from github_integration import GitHubIntegrationError


router = APIRouter(prefix="/api/integrations/github/full-release", tags=["Full Release"])


class ConfirmedManifestRequest(BaseModel):
    manifest_id: str
    confirmed: bool = False


class ConfirmedInstallRequest(BaseModel):
    confirmed: bool = False


def _http_error(error: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(error, "status_code", 502),
        detail=str(error),
        headers={"X-MangaX-Full-Release-Error": getattr(error, "code", "full_release_error")},
    )


@router.get("")
def latest_full_release() -> dict[str, Any]:
    try:
        return full_release_manager.latest_manifest()
    except (FullReleaseError, GitHubIntegrationError) as error:
        raise _http_error(error) from error


@router.post("/download")
def start_full_release_download(request: ConfirmedManifestRequest) -> dict[str, Any]:
    try:
        return full_release_manager.start_download(request.manifest_id, confirmed=request.confirmed)
    except (FullReleaseError, GitHubIntegrationError) as error:
        raise _http_error(error) from error


@router.get("/download/{job_id}")
def full_release_download_status(
    job_id: str = Path(min_length=16, max_length=200),
) -> dict[str, Any]:
    try:
        return full_release_manager.download_status(job_id)
    except FullReleaseError as error:
        raise _http_error(error) from error


@router.delete("/download/{job_id}")
def cancel_full_release_download(
    job_id: str = Path(min_length=16, max_length=200),
) -> dict[str, Any]:
    try:
        return full_release_manager.cancel_download(job_id)
    except FullReleaseError as error:
        raise _http_error(error) from error


@router.post("/download/{job_id}/install")
def install_full_release(
    request: ConfirmedInstallRequest,
    job_id: str = Path(min_length=16, max_length=200),
) -> dict[str, Any]:
    try:
        return full_release_manager.install(job_id, confirmed=request.confirmed)
    except FullReleaseError as error:
        raise _http_error(error) from error
