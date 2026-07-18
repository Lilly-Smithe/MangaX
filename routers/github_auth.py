"""Reader ve Full tarafından paylaşılan GitHub hesap doğrulama uçları."""

from __future__ import annotations

import threading
import webbrowser
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query

from mangax.integrations.github_integration import GitHubIntegrationError, github_integration_manager


router = APIRouter(prefix="/api/integrations/github", tags=["GitHub Auth"])


def _http_error(error: GitHubIntegrationError) -> HTTPException:
    return HTTPException(
        status_code=getattr(error, "status_code", 502),
        detail=str(error),
        headers={"X-MangaX-GitHub-Error": getattr(error, "code", "github_error")},
    )


@router.get("/status")
def github_status() -> dict[str, Any]:
    return github_integration_manager.status()


@router.post("/connect")
def connect_github() -> dict[str, Any]:
    try:
        result = github_integration_manager.start_device_flow()
    except GitHubIntegrationError as error:
        raise _http_error(error) from error
    threading.Thread(
        target=webbrowser.open,
        args=(result["verification_uri"],),
        daemon=True,
    ).start()
    return result


@router.get("/connect/status")
def poll_github_connection(
    request_id: str = Query(min_length=16, max_length=200),
) -> dict[str, Any]:
    try:
        return github_integration_manager.poll_device_flow(request_id)
    except GitHubIntegrationError as error:
        raise _http_error(error) from error


@router.delete("/connect/{request_id}")
def cancel_github_connection(
    request_id: str = Path(min_length=16, max_length=200),
) -> dict[str, Any]:
    return github_integration_manager.cancel_device_flow(request_id)


@router.delete("/connection")
def disconnect_github() -> dict[str, Any]:
    try:
        return github_integration_manager.disconnect()
    except GitHubIntegrationError as error:
        raise _http_error(error) from error
