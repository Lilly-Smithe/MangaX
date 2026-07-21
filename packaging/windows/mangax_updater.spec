# MangaX'in yalnız standart kütüphane kullanan görünmeyen güncelleme yardımcısı.
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), '..', '..'))
a = Analysis(
    [os.path.join(ROOT, 'packaging', 'windows', 'mangax_updater.py')],
    pathex=[ROOT], binaries=[], datas=[], hiddenimports=[], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=['tkinter', 'unittest'], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name='MangaX-Updater',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    console=False, disable_windowed_traceback=True,
)
