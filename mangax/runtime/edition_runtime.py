"""Public Reader çekirdek servis yaşam döngüsü."""

from mangax.core.dependencies import close_core_services, start_core_services

def configure_services() -> None:
    return None

def start_services() -> None:
    start_core_services()

def close_services() -> None:
    close_core_services()
