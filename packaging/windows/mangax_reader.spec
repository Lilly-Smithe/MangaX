# MangaX Reader PyInstaller profili. Full edition modülleri fiziksel olarak dışlanır.

import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None
ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), '..', '..'))

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
    'routers.updates',
    'mangax',
    'mangax.core',
    'mangax.core.dependencies',
    'mangax.core.library',
    'mangax.reader',
    'mangax.reader.local_importer',
    'mangax.reader.local_import_jobs',
    'mangax.integrations',
    'mangax.integrations.github_integration',
    'mangax.integrations.full_release',
    'mangax.integrations.app_update',
    'mangax.integrations.secure_store',
    'mangax.runtime',
    'mangax.runtime.edition_runtime',
    'mangax.runtime.router_registry',
    'mangax.runtime.shared_data_migration',
    'PIL',
    'PIL.Image',
    'PIL.ImageOps',
    'mangax.core.database',
    'mangax.core.backup_service',
    'mangax.core.preferences_manager',
    'mangax.core.models',
    'mangax.core.config',
    'certifi',
    'ssl',
    'psutil',
    'json',
]

reader_excludes = [
    'mangax.full',
    'mangax.full.anilist',
    'mangax.full.chapter_tracker',
    'mangax.full.downloader',
    'mangax.full.extension_manager',
    'extension_store',
    'mangax.full.image_optimizer',
    'mangax.full.mal_integration',
    'mangax.full.manga_matcher',
    'mangax.full.site_analyzer',
    'mangax.full.sources_manager',
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
    [os.path.join(ROOT, 'app_gui.py')],
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
