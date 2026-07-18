@echo off
setlocal
cd /d "%~dp0"
set "MANGAX_EDITION=reader"
python -m PyInstaller packaging\windows\mangax_reader.spec --noconfirm --clean
if errorlevel 1 exit /b 1
xcopy /E /I /Y "static" "dist\MangaX-Reader\static" >nul
