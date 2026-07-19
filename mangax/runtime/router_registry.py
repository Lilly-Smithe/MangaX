"""Public MangaX Reader için yalnızca yerel çekirdek router kaydı."""

from importlib import import_module
from typing import Iterable

CORE_ROUTER_MODULES = (
    "routers.frontend",
    "routers.library",
    "routers.local_reader",
    "routers.github_auth",
    "routers.full_release",
    "routers.updates",
    "routers.mal",
    "routers.backup",
    "routers.preferences",
    "routers.diagnostics",
)

def router_module_names(edition: str = "reader") -> tuple[str, ...]:
    if str(edition or "").strip().lower() != "reader":
        raise ValueError("Bu kaynak paketi yalnızca Reader edition içerir.")
    return CORE_ROUTER_MODULES

def register_edition_routers(app, edition: str = "reader") -> Iterable[str]:
    modules = router_module_names(edition)
    for module_name in modules:
        app.include_router(import_module(module_name).router)
    return modules
