# MangaX Reader PyInstaller profili. Full edition modülleri fiziksel olarak dışlanır.

import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None
ROOT = os.path.abspath(os.path.dirname(SPEC))

datas = []
try:
    import certifi
    datas += [(certifi.where(), 'certifi')]
except Exception:
    pass

try:
    wv_datas, wv_binaries, wv_hiddenimports = collect_all('webview')
    datas += wv_datas
except Exception:
    wv_binaries = []
    wv_hiddenimports = []

hiddenimports = [
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'fastapi',
    'fastapi.staticfiles',
    'fastapi.middleware.cors',
    'starlette',
    'starlette.staticfiles',
    'starlette.routing',
    'starlette.middleware',
    'starlette.middleware.cors',
    'anyio',
    'anyio._backends._asyncio',
    *wv_hiddenimports,
    'routers',
    'routers.frontend',
    'routers.library',
    'routers.local_reader',
    'routers.github_auth',
    'routers.full_release',
    'routers.backup',
    'routers.preferences',
    'routers.diagnostics',
    'core_dependencies',
    'edition_runtime',
    'router_registry',
    'library',
    'local_importer',
    'local_import_jobs',
    'github_integration',
    'full_release',
    'shared_data_migration',
    'secure_store',
    'PIL',
    'PIL.Image',
    'PIL.ImageOps',
    'database',
    'backup_service',
    'preferences_manager',
    'models',
    'config',
    'certifi',
    'ssl',
    'psutil',
    'json',
]

reader_excludes = [
    'anilist',
    'chapter_tracker',
    'downloader',
    'extension_manager',
    'extension_store',
    'image_optimizer',
    'mal_integration',
    'manga_matcher',
    'site_analyzer',
    'sources_manager',
    'scrapers',
    'routers.search',
    'routers.manga',
    'routers.downloads',
    'routers.sources',
    'routers.news',
    'routers.extensions',
    'routers.tracker',
    'routers.mal',
    'routers.github',
    'selenium',
    'cloudscraper',
    'bs4',
    'requests',
    'tkinter',
    'matplotlib',
    'numpy',
    'pandas',
    'scipy',
    'PyQt5',
    'PyQt6',
    'wx',
    'gtk',
    'test',
    'unittest',
    'lxml',
    'trio',
]

a = Analysis(
    ['app_gui.py'],
    pathex=[ROOT],
    binaries=wv_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(ROOT, 'packaging', 'runtime_reader.py')],
    excludes=reader_excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MangaX-Reader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MangaX-Reader',
)
