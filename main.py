import time
import threading
import subprocess
import webbrowser
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mangax.core.config import STATIC_DIR, DOWNLOADS_DIR, LOCAL_MANGA_DIR, HOST, PORT, APP_URL, IS_FULL_EDITION
from mangax.runtime.shared_data_migration import migrate_shared_user_data
migrate_shared_user_data()
from mangax.runtime.edition_runtime import start_services, close_services
from mangax.core.backup_service import local_backup_manager
from mangax.core.migrate_folders import migrate_downloads
from mangax.runtime.router_registry import register_edition_routers
from mangax.core.local_api_security import configure_local_api_security

@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate_downloads()
    start_services()
    local_backup_manager.start()
    _browser_proc = None

    def open_browser():
        import os
        if os.environ.get('MANGAX_WAS_RUNNING') == '1':
            print('[MangaX] Eski oturum algılandı, yeni tarayıcı sekmesi açılmadı. Mevcut sekmenizi yenileyebilirsiniz.')
            return
        nonlocal _browser_proc
        time.sleep(1.8)
        try:
            _browser_proc = subprocess.Popen(['cmd', '/c', 'start', '', APP_URL], shell=False, creationflags=134217728)
        except Exception:
            webbrowser.open(APP_URL)
    threading.Thread(target=open_browser, daemon=True).start()
    yield
    local_backup_manager.stop(create_final=True)
    close_services()
    _close_app_tabs()
app = FastAPI(title='MangaX API', description='Manga Downloader and Reader Backend', lifespan=lifespan)
configure_local_api_security(app, host=HOST, port=PORT)
register_edition_routers(app)
app.mount('/downloads', StaticFiles(directory=DOWNLOADS_DIR), name='downloads')
app.mount('/local-manga', StaticFiles(directory=LOCAL_MANGA_DIR), name='local-manga')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')

def _close_app_tabs():
    """
    Uygulama kapanırken veya açılırken arkada kalan headless Chrome ve ChromeDriver süreçlerini temizler.
    Kullanıcının normal Chrome sekmelerine dokunmaz.
    """
    try:
        import psutil
        killed = 0
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = (proc.info.get('name') or '').lower()
                if 'chrome' not in name and 'chromedriver' not in name:
                    continue
                if 'chromedriver' in name:
                    proc.terminate()
                    killed += 1
                    continue
                cmdline = proc.info.get('cmdline') or []
                cmdline_str = ' '.join(cmdline).lower()
                if '--headless' in cmdline_str:
                    proc.terminate()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if killed:
            print(f'[MangaX] {killed} headless Chrome/ChromeDriver süreci kapatıldı.')
    except Exception as e:
        print(f'[MangaX] Chrome/ChromeDriver temizleme hatası: {e}')

def _free_port(port: int) -> bool:
    """Port kullanımdaysa temizle ve True dön"""
    import psutil
    killed_any = False
    try:
        for conn in psutil.net_connections(kind='inet'):
            if not conn.laddr:
                continue
            laddr_port = getattr(conn.laddr, 'port', None)
            if laddr_port != port or conn.status != 'LISTEN':
                continue
            if not conn.pid:
                continue
            proc = None
            try:
                proc = psutil.Process(conn.pid)
                print(f'[MangaX] Port {port} kullanımda (PID {conn.pid}). Kapatılıyor...')
                proc.terminate()
                proc.wait(timeout=3)
                killed_any = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                if proc is not None:
                    try:
                        proc.kill()
                        killed_any = True
                    except Exception:
                        pass
    except Exception as e:
        print(f'[MangaX] Port temizleme hatası: {e}')
    return killed_any
if __name__ == '__main__':
    import os
    was_running = _free_port(PORT)
    if was_running:
        os.environ['MANGAX_WAS_RUNNING'] = '1'
    _close_app_tabs()
    time.sleep(0.8)
    uvicorn.run('main:app', host=HOST, port=PORT, reload=False)
