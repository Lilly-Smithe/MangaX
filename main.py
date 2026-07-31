import time
import threading
import subprocess
import webbrowser
import socket
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mangax.core.config import STATIC_DIR, DOWNLOADS_DIR, LOCAL_MANGA_DIR, HOST, PORT, APP_URL, IS_FULL_EDITION
from mangax.runtime.shared_data_migration import migrate_shared_user_data
try:
    migrate_shared_user_data()
except Exception as migration_error:
    print(f'[MangaX] Ortak veri geçişi atlandı: {type(migration_error).__name__}')
from mangax.runtime.edition_runtime import configure_services, start_services, close_services
from mangax.core.backup_service import local_backup_manager
from mangax.core.migrate_folders import migrate_downloads
from mangax.runtime.router_registry import register_edition_routers
from mangax.core.local_api_security import configure_local_api_security

@asynccontextmanager
async def lifespan(app: FastAPI):

    def start_deferred_services():
        for label, action in (('edition servisleri', start_services), ('yerel yedekleme', local_backup_manager.start), ('eski indirmeleri taşıma', migrate_downloads)):
            try:
                action()
            except Exception as error:
                print(f'[MangaX] Başlangıç adımı atlandı ({label}): {error}')
    threading.Thread(target=start_deferred_services, name='MangaXDeferredStartup', daemon=True).start()
    _browser_proc = None

    def open_browser():
        import os
        if os.environ.get('MANGAX_WAS_RUNNING') == '1':
            print('[MangaX] Eski oturum algılandı, yeni tarayıcı sekmesi açılmadı. Mevcut sekmenizi yenileyebilirsiniz.')
            return
        nonlocal _browser_proc
        if not _wait_for_local_server(HOST, PORT, timeout=10.0):
            print('[MangaX] Tarayıcı açılmadan önce yerel sunucu hazır olmadı.')
            return
        try:
            _browser_proc = subprocess.Popen(['cmd', '/c', 'start', '', APP_URL], shell=False, creationflags=134217728)
        except Exception:
            webbrowser.open(APP_URL)
    threading.Thread(target=open_browser, daemon=True).start()
    yield
    local_backup_manager.stop(create_final=True)
    close_services()
app = FastAPI(title='MangaX API', description='Manga Downloader and Reader Backend', lifespan=lifespan)
configure_local_api_security(app, host=HOST, port=PORT)
register_edition_routers(app)
configure_services()
app.mount('/downloads', StaticFiles(directory=DOWNLOADS_DIR), name='downloads')
app.mount('/local-manga', StaticFiles(directory=LOCAL_MANGA_DIR), name='local-manga')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')

def _wait_for_local_server(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.05)
    return False

def _port_is_available(host: str, port: int) -> bool:
    """Port sahibini sonlandırmadan kullanılabilirliği denetle."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
        return True
    except OSError:
        return False
if __name__ == '__main__':
    if not _port_is_available(HOST, PORT):
        raise SystemExit(f'MangaX başlatılamadı: {HOST}:{PORT} başka bir uygulama tarafından kullanılıyor.')
    uvicorn.run('main:app', host=HOST, port=PORT, reload=False)
