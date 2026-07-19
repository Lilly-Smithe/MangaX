import os
import sys
APP_VERSION = 'v0.13.4'
SUPPORTED_EDITIONS = {'reader'}
APP_EDITION = os.getenv('MANGAX_EDITION', 'reader').strip().lower() or 'full'
if APP_EDITION not in SUPPORTED_EDITIONS:
    raise RuntimeError(f'Geçersiz MangaX edition değeri: {APP_EDITION!r}. Desteklenen değer: reader')
IS_READER_EDITION = APP_EDITION == 'reader'
IS_FULL_EDITION = APP_EDITION == 'full'
current_file_path = os.path.abspath(__file__)
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = os.path.abspath(getattr(sys, '_MEIPASS', BASE_DIR))
elif '_internal' in current_file_path:
    BUNDLE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path)))
    BASE_DIR = os.path.dirname(BUNDLE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(current_file_path)))
    BUNDLE_DIR = BASE_DIR
STATIC_DIR = os.path.join(BASE_DIR, 'static')
LEGACY_DATA_DIR = os.path.join(BASE_DIR, 'data')
_local_app_data = os.environ.get('LOCALAPPDATA', '').strip()
_packaged_runtime = bool(getattr(sys, 'frozen', False) or '_internal' in current_file_path)
_shared_data_default = os.path.join(_local_app_data, 'MangaX', 'data') if _local_app_data and _packaged_runtime else LEGACY_DATA_DIR
DATA_DIR = os.path.abspath(os.getenv('MANGAX_DATA_DIR', _shared_data_default))
DEFAULT_DOWNLOADS_DIR = os.path.join(BASE_DIR, 'downloads')
DOWNLOADS_DIR = DEFAULT_DOWNLOADS_DIR
try:
    import json
    _preferences_path = os.path.join(DATA_DIR, 'app_preferences.json')
    if os.path.isfile(_preferences_path):
        with open(_preferences_path, 'r', encoding='utf-8') as _preferences_file:
            _configured_downloads = str(json.load(_preferences_file).get('download_directory') or '').strip()
        if _configured_downloads:
            DOWNLOADS_DIR = os.path.abspath(os.path.expanduser(_configured_downloads))
except (OSError, ValueError, TypeError):
    pass
LOCAL_MANGA_DIR = os.path.abspath(os.getenv('MANGAX_LOCAL_MANGA_DIR', os.path.join(_local_app_data, 'MangaX', 'local_manga') if _local_app_data else os.path.join(DATA_DIR, 'local_manga')))
SOURCE_REPORTS_DIR = os.path.join(BASE_DIR, 'kaynak_raporlari')
BACKUPS_DIR = os.path.join(DATA_DIR, 'backups')
EXTENSIONS_DIR = os.path.join(DATA_DIR, 'extensions')
HOST = '127.0.0.1'
PORT = 8000
APP_URL = f'http://localhost:{PORT}'
GITHUB_ACCESS_REPOSITORY = os.getenv('MANGAX_GITHUB_ACCESS_REPOSITORY', 'MangaX-App/mangax-full-releases').strip()
GITHUB_READER_RELEASE_REPOSITORY = os.getenv('MANGAX_READER_RELEASE_REPOSITORY', 'Lilly-Smithe/MangaX').strip()
GITHUB_FULL_RELEASE_MANIFEST_PATH = os.getenv('MANGAX_FULL_RELEASE_MANIFEST_PATH', 'releases/latest.json').strip().strip('/') or 'releases/latest.json'
GITHUB_OAUTH_CLIENT_ID = os.getenv('MANGAX_GITHUB_CLIENT_ID', 'Ov23li2UlSSwaQNJHeyA').strip()
MAL_OAUTH_CLIENT_ID = os.getenv('MANGAX_MAL_CLIENT_ID', '27002aa2bf9efbe7be68020cf89b843d').strip()
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOCAL_MANGA_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(SOURCE_REPORTS_DIR, exist_ok=True)
os.makedirs(BACKUPS_DIR, exist_ok=True)
