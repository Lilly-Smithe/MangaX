@echo off
setlocal
cd /d "%~dp0"
set "MANGAX_EDITION=reader"
python app_gui.py
