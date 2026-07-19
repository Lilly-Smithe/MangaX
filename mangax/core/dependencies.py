"""Reader ve Full sürümlerinin paylaştığı hafif çalışma zamanı bağımlılıkları."""

from mangax.core.library import LibraryManager


library_manager = LibraryManager()


def start_core_services() -> None:
    """Ortak arka plan yöneticilerini yeni uygulama yaşam döngüsüne hazırla."""
    from mangax.integrations.mal_outbound import mal_outbound_service
    from mangax.integrations.mal_sync_jobs import mal_sync_job_manager

    mal_sync_job_manager.start_lifecycle()
    mal_outbound_service.start()


def close_core_services() -> None:
    """Yarım MAL transaction'ını güvenli iptal edip iş parçacığını sonlandır."""
    from mangax.integrations.mal_outbound import mal_outbound_service
    from mangax.integrations.mal_sync_jobs import mal_sync_job_manager
    from mangax.integrations.mal_sync_scheduler import mal_sync_scheduler

    mal_sync_scheduler.shutdown()
    mal_outbound_service.shutdown()
    mal_sync_job_manager.shutdown()
