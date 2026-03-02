@echo off
cd /d "%~dp0"
echo Starting mouse debug script...
echo Press any mouse button (especially XButton1/XButton2)
echo Close this window or press Ctrl+C to stop
echo.
python debug_mouse_all.py
pause
