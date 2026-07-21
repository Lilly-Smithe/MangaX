# app_gui.py
# MangaX - Masaustu Uygulamasi Giris Noktasi
# Tarayici acmak yerine yerli bir Windows penceresi (WebView2) kullanir.
#
# Kullanim:
#   python app_gui.py
#
# Gereksinim:
#   pip install pywebview

import sys
import time
import threading
import socket
import os
import io
import traceback

# ── Frozen exe: calisma dizinini exe'nin konumuna ayarla ─────────────────────
if getattr(sys, "frozen", False):
    _exe_dir = os.path.dirname(sys._MEIPASS)
    os.chdir(_exe_dir)

# ── Encoding duzeltmesi (Windows terminali UTF-8 icin) ───────────────────────
os.environ["PYTHONIOENCODING"] = "utf-8"

# ── Log dosyasi (frozen modda hatalari kaydet) ────────────────────────────────
if getattr(sys, "frozen", False):
    _log_root = os.path.join(
        os.environ.get("LOCALAPPDATA", "").strip() or os.path.dirname(sys._MEIPASS),
        "MangaX",
        "logs",
    )
    _log_path = os.path.join(_log_root, "mangax-startup.log")
    try:
        os.makedirs(_log_root, exist_ok=True)
        _log_file = open(_log_path, "w", encoding="utf-8", errors="replace")
        sys.stdout = _log_file
        sys.stderr = _log_file
    except Exception:
        pass
else:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── Frozen (PyInstaller exe) SSL sertifika duzeltmesi ────────────────────────
# httpx/ssl, certifi'nin cacert.pem dosyasini exe icinde bulamaz.
# _MEIPASS altindaki certifi klasorune yonlendiriyoruz.
if getattr(sys, "frozen", False):
    _meipass = getattr(sys, "_MEIPASS", "")
    if _meipass:
        _cacert = os.path.join(_meipass, "certifi", "cacert.pem")
        if os.path.isfile(_cacert):
            os.environ.setdefault("SSL_CERT_FILE", _cacert)
            os.environ.setdefault("REQUESTS_CA_BUNDLE", _cacert)


# ── Config ───────────────────────────────────────────────────────────────────
from mangax.core.config import HOST, PORT, APP_URL, DOWNLOADS_DIR, LOCAL_MANGA_DIR, STATIC_DIR, BASE_DIR
from mangax.runtime.shared_data_migration import migrate_shared_user_data

try:
    migrate_shared_user_data()
except Exception as _migration_error:
    print(f"[MangaX] Ortak veri geçişi atlandı: {_migration_error}", flush=True)
    traceback.print_exc()

from mangax.runtime.edition_runtime import start_services, close_services
from mangax.core.backup_service import local_backup_manager
from mangax.core.migrate_folders import migrate_downloads

print(f"[MangaX DEBUG] sys.frozen: {getattr(sys, 'frozen', False)}", flush=True)
print(f"[MangaX DEBUG] sys.executable: {sys.executable}", flush=True)
print(f"[MangaX DEBUG] config.BASE_DIR: {BASE_DIR}", flush=True)
print(f"[MangaX DEBUG] config.DOWNLOADS_DIR: {DOWNLOADS_DIR}", flush=True)

from mangax.runtime.router_registry import register_edition_routers


class MangaXDesktopBridge:
    """Web arayüzüne yalnızca yerel dosya seçimi ve içe aktarma yeteneği verir."""

    def __init__(self):
        # pywebview js_api bütün public nesne özniteliklerini özyinelemeli tarar.
        # Window gibi native nesneler bu yüzden mutlaka private tutulmalıdır.
        self._window = None
        self._installer_started = False
        from mangax.reader.local_import_jobs import LocalImportJobManager
        self._local_imports = LocalImportJobManager()
        from mangax.integrations.full_release import full_release_manager
        full_release_manager.set_installer_launcher(self._launch_full_installer)
        from mangax.integrations.app_update import app_update_manager
        app_update_manager.set_installer_launcher(self._launch_app_update_installer)

    def _attach_window(self, window) -> None:
        self._window = window

    def start_local_manga_import(self, selection_type: str) -> dict:
        try:
            import webview

            if self._window is None:
                raise RuntimeError("MangaX penceresi hazır değil.")
            if selection_type == "folder":
                selected = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            elif selection_type == "file":
                selected = self._window.create_file_dialog(
                    webview.OPEN_DIALOG,
                    allow_multiple=False,
                    file_types=("Manga dosyaları (*.zip;*.cbz;*.jpg;*.jpeg;*.png;*.webp)",),
                )
            else:
                raise ValueError("Geçersiz seçim türü.")
            if not selected:
                return {"status": "cancelled"}
            path = selected[0] if isinstance(selected, (list, tuple)) else selected
            return self._local_imports.start(path)
        except Exception as error:
            print(f"[MangaX] Yerel manga içe aktarma hatası: {error}", flush=True)
            return {"status": "error", "message": str(error)}

    def get_local_manga_import(self, job_id: str) -> dict:
        return self._local_imports.status(job_id)

    def cancel_local_manga_import(self, job_id: str) -> dict:
        return self._local_imports.cancel(job_id)

    def _launch_full_installer(self, installer_path: str) -> bool:
        # Korunan Full yukselme sozlesmesi genel handoff icinde uygulanir:
        # local_backup_manager.create("before_full_install")
        return self._launch_verified_installer(installer_path, "before_full_install")

    def _launch_app_update_installer(self, installer_path: str) -> bool:
        return self._launch_verified_installer(installer_path, "before_app_update")

    def _launch_verified_installer(self, installer_path: str, backup_label: str) -> bool:
        from pathlib import Path
        import subprocess

        path = Path(installer_path).resolve()
        if self._installer_started or not path.is_file() or path.suffix.lower() not in {".exe", ".msi"}:
            return False
        self._installer_started = True

        def handoff() -> None:
            local_backup_manager.create(backup_label)
            if self._window is not None:
                try:
                    self._window.destroy()
                except Exception:
                    pass
            time.sleep(1.0)
            if path.suffix.lower() == ".msi":
                command = ["msiexec.exe", "/i", str(path)]
            else:
                # Reader baska bir disk veya klasore kurulmus olabilir. Full
                # installer ayni AppId'yi kullanir; /DIR mevcut calisan EXE'nin
                # gercek konumunu ayrica sabitleyerek ozel yolu da korur.
                command = [str(path), f"/DIR={BASE_DIR}"]
            subprocess.Popen(
                command,
                cwd=str(path.parent),
                shell=False,
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )

        threading.Thread(target=handoff, name="MangaXInstallerHandoff", daemon=False).start()
        return True


# ── FastAPI kurulumu ─────────────────────────────────────────────────────────

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mangax.core.local_api_security import configure_local_api_security

api = FastAPI(title="MangaX API", description="Manga Downloader and Reader Backend")

configure_local_api_security(api, host=HOST, port=PORT)

register_edition_routers(api)

api.mount("/downloads", StaticFiles(directory=DOWNLOADS_DIR), name="downloads")
api.mount("/local-manga", StaticFiles(directory=LOCAL_MANGA_DIR), name="local-manga")
api.mount("/static",    StaticFiles(directory=STATIC_DIR),    name="static")


# ── Port temizleme (main.py'den alındı) ─────────────────────────────────────

def _port_is_available(host: str, port: int) -> bool:
    """Başka MangaX veya ilgisiz bir uygulamayı sonlandırmadan portu denetle."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
        return True
    except OSError:
        return False


_instance_mutex = None


def _acquire_single_instance() -> bool:
    """Reader ve Full için ortak Windows tek-örnek kilidi."""
    global _instance_mutex
    if sys.platform != "win32":
        return True
    import ctypes

    handle = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\MangaX.Desktop")
    if not handle:
        return False
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True


def _release_single_instance() -> None:
    global _instance_mutex
    if _instance_mutex and sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.ReleaseMutex(_instance_mutex)
            ctypes.windll.kernel32.CloseHandle(_instance_mutex)
        except Exception:
            pass
    _instance_mutex = None


def _run_startup_step(label: str, action) -> bool:
    """İkincil bakım servisleri arayüzün açılmasını engellemesin."""
    try:
        action()
        return True
    except Exception as error:
        print(f"[MangaX] Başlangıç adımı atlandı ({label}): {error}", flush=True)
        traceback.print_exc()
        return False


def _close_headless_chrome():
    try:
        import psutil
        killed = 0
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if "chromedriver" in name:
                    proc.terminate(); killed += 1; continue
                if "chrome" in name:
                    cmdline_str = " ".join(proc.info.get("cmdline") or []).lower()
                    if "--headless" in cmdline_str:
                        proc.terminate(); killed += 1
            except Exception:
                pass
        if killed:
            print(f"[MangaX] {killed} headless Chrome/ChromeDriver süreci kapatıldı.")
    except Exception as e:
        print(f"[MangaX] Chrome temizleme hatası: {e}")


# ── Sunucunun hazır olmasını bekle ───────────────────────────────────────────

def _wait_for_server(host: str, port: int, timeout: float = 15.0) -> bool:
    """Sunucu porta cevap verene kadar bekle. / Wait until server is ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


# ── Sunucu thread'i ──────────────────────────────────────────────────────────

_uvicorn_server: uvicorn.Server | None = None
_server_exception = None

def _run_server():
    global _uvicorn_server, _server_exception
    try:
        print("[MangaX] Sunucu thread'i basladi", flush=True)
        import asyncio

        # PyInstaller frozen exe'de asyncio event loop sorunlarini gidermek icin
        # yeni bir event loop olustur ve ProactorEventLoop kullan (Windows icin)
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(loop)
        else:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        config = uvicorn.Config(
            api,
            host=HOST,
            port=PORT,
            reload=False,
            log_level="info",
            loop="none",   # uvicorn'un kendi loop'unu acmaya calismamasi icin
        )
        _uvicorn_server = uvicorn.Server(config)
        print("[MangaX] uvicorn serve() cagriliyor...", flush=True)
        loop.run_until_complete(_uvicorn_server.serve())
        print("[MangaX] uvicorn serve() tamamlandi.", flush=True)
    except Exception as _srv_err:
        _server_exception = _srv_err
        print(f"[MangaX SUNUCU HATA] {_srv_err}", flush=True)
        traceback.print_exc()
        try:
            sys.stdout.flush()
        except Exception:
            pass


def _stop_server():
    global _uvicorn_server
    if _uvicorn_server:
        _uvicorn_server.should_exit = True


# ── Pencere kapanma geri çağrısı ─────────────────────────────────────────────

_shutdown_lock = threading.Lock()
_shutdown_complete = False


def _on_window_closed():
    """Kullanici pencereyi kapatinca temizlik yap."""
    global _shutdown_complete
    with _shutdown_lock:
        if _shutdown_complete:
            return
        _shutdown_complete = True
    print("[MangaX] Pencere kapatildi. Sunucu durduruluyor...")
    for label, action in (
        ("son yedek", lambda: local_backup_manager.stop(create_final=True)),
        ("servisler", close_services),
        ("tarayıcı temizliği", _close_headless_chrome),
        ("yerel sunucu", _stop_server),
        ("tek örnek kilidi", _release_single_instance),
    ):
        try:
            action()
        except Exception as error:
            print(f"[MangaX] Kapanış adımı tamamlanamadı ({label}): {error}", flush=True)


# ── Ana giriş noktası ────────────────────────────────────────────────────────

def main():
    # 1. Aynı anda iki örneğin birbirini kapatmasını engelle.
    if not _acquire_single_instance():
        print("[MangaX] Uygulama zaten çalışıyor; ikinci örnek açılmadı.", flush=True)
        return
    if not _port_is_available(HOST, PORT):
        print(f"[MangaX HATA] {PORT} portu başka bir uygulama tarafından kullanılıyor.", flush=True)
        _release_single_instance()
        return
    _close_headless_chrome()

    # 2. Bakım servislerindeki tekil bir hata pencerenin açılmasını engellemez.
    _run_startup_step("eski indirmeleri taşıma", migrate_downloads)
    _run_startup_step("edition servisleri", start_services)
    _run_startup_step("yerel yedekleme", local_backup_manager.start)

    # 3. FastAPI'yi arka planda baslat
    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()
    print(f"[MangaX] Sunucu baslatiliyor: {APP_URL}")

    # 4. Sunucunun hazir olmasini bekle
    if not _wait_for_server(HOST, PORT, timeout=30):
        print("[MangaX HATA] Sunucu 30 saniye icinde baslamadi!")
        if _server_exception:
            print(f"[MangaX SUNUCU ISTISNA] {_server_exception}")
        try:
            sys.stdout.flush()
        except Exception:
            pass
        _on_window_closed()
        sys.exit(1)
    print("[MangaX] Sunucu hazir. Pencere aciliyor...")

    # 5. WebView penceresi aç
    try:
        import webview  # pywebview
    except ImportError:
        print(
            "\n[MangaX HATA] 'pywebview' paketi bulunamadi.\n"
            "Lutfen sunu calistirin:  pip install pywebview\n"
        )
        _on_window_closed()
        sys.exit(1)

    desktop_bridge = MangaXDesktopBridge()
    window = webview.create_window(
        title="MangaX",
        url=APP_URL,
        width=1400,
        height=900,
        min_size=(900, 600),
        resizable=True,
        text_select=True,
        confirm_close=False,
        js_api=desktop_bridge,
    )
    desktop_bridge._attach_window(window)
    window.events.closed += _on_window_closed

    # Windows'ta oncelikle WebView2 (edgechromium), yoksa winforms dene
    _gui_backends = ["edgechromium", "winforms"]
    _started = False
    for _backend in _gui_backends:
        try:
            print(f"[MangaX] GUI backend deneniyor: {_backend}")
            webview.start(gui=_backend, debug=False)
            _started = True
            break
        except Exception as _e:
            print(f"[MangaX] {_backend} basarisiz: {_e}")
            continue
    if not _started:
        print("[MangaX HATA] Hicbir GUI backend calismiyor. WebView2 Runtime kurulu mu?")
        print("  https://developer.microsoft.com/en-us/microsoft-edge/webview2/ adresinden indirin.")
        _on_window_closed()
        sys.exit(1)


if __name__ == "__main__":
    try:
        print("[MangaX] Uygulama baslatiliyor...")
        main()
    except Exception as _e:
        print(f"[MangaX KRITIK HATA] {_e}")
        traceback.print_exc()
        # Log dosyasini kapat ki disk'e yazilsin
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        raise
