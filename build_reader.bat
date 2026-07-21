@echo off
setlocal
cd /d "%~dp0"
set "MANGAX_EDITION=reader"
python -m PyInstaller packaging\windows\mangax_reader.spec --noconfirm --clean
if errorlevel 1 exit /b 1
python -m PyInstaller packaging\windows\mangax_updater.spec --noconfirm --clean
if errorlevel 1 exit /b 1
copy /Y "dist\MangaX-Updater.exe" "dist\MangaX-Reader\MangaX-Updater.exe" >nul
xcopy /E /I /Y "static" "dist\MangaX-Reader\static" >nul
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\build_reader_installer.ps1
