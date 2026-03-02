@echo off
call "%~dp0RitsuStop.cmd"
timeout /t 1 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0RitsuBoot.ps1"

