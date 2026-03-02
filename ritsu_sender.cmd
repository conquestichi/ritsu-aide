@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ritsu_send_to_file.ps1"
exit /b %ERRORLEVEL%