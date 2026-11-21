@echo off
chcp 65001 >nul
cd /d "%~dp0"
"D:\Python\python.exe" "youtube_dl_gui.py"
if errorlevel 1 pause
