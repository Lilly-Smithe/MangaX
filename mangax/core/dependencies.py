"""Reader ve Full sürümlerinin paylaştığı hafif çalışma zamanı bağımlılıkları."""

from mangax.core.library import LibraryManager


library_manager = LibraryManager()


def start_core_services() -> None:
    """Çekirdek servisler şu anda arka plan iş parçacığı gerektirmiyor."""


def close_core_services() -> None:
    """Çekirdek servisler için ayrılmış simetrik kapanış noktası."""
