@echo off
echo [律] 自動起動開始...

REM VOICEVOX起動 (起動していなければ)
tasklist /FI "IMAGENAME eq VOICEVOX.exe" 2>NUL | find /I "VOICEVOX.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    echo [律] VOICEVOX起動中...
    start "" "C:\Users\conqu\AppData\Local\Programs\VOICEVOX\VOICEVOX.exe" --no-gpu
    timeout /t 8 /nobreak >NUL
)

REM VMagicMirror起動 (起動していなければ)
tasklist /FI "IMAGENAME eq VMagicMirror.exe" 2>NUL | find /I "VMagicMirror.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    echo [律] VMagicMirror起動中...
    start "" "C:\Users\conqu\VMagicMirror_v4.0.1\VMagicMirror.exe"
    timeout /t 3 /nobreak >NUL
)

REM 律V4起動
echo [律] ritsu_v4.py 起動...
cd /d C:\Users\conqu\Desktop\ritsu-aide
python ritsu_v4.py
