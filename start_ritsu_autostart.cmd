@echo off
REM 律 v3 自動起動スクリプト (VOICEVOX自動起動 + 待機 + ritsu.py起動)
cd /d "%~dp0"

REM ログファイル
set LOG_DIR=%LOCALAPPDATA%\RitsuWorker
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set LOG_FILE=%LOG_DIR%\autostart_%date:~0,4%%date:~5,2%%date:~8,2%.log

echo [%date% %time%] Ritsu autostart begin >> "%LOG_FILE%"

REM --- VOICEVOX 起動 ---
set VOICEVOX_EXE=%LOCALAPPDATA%\Programs\VOICEVOX\VOICEVOX.exe
set VOICEVOX_URL=http://127.0.0.1:50021/speakers
set MAX_WAIT=90

REM 既に応答するならスキップ
curl -s -o nul -w "%%{http_code}" "%VOICEVOX_URL%" 2>nul | findstr "200" >nul
if %ERRORLEVEL% EQU 0 (
    echo VOICEVOX is already running.
    echo [%date% %time%] VOICEVOX already running >> "%LOG_FILE%"
    goto START_RITSU
)

REM VOICEVOX起動
if exist "%VOICEVOX_EXE%" (
    echo Starting VOICEVOX...
    echo [%date% %time%] Starting VOICEVOX: %VOICEVOX_EXE% >> "%LOG_FILE%"
    start "" "%VOICEVOX_EXE%"
) else (
    echo WARNING: VOICEVOX not found at %VOICEVOX_EXE%
    echo [%date% %time%] VOICEVOX not found: %VOICEVOX_EXE% >> "%LOG_FILE%"
    goto START_RITSU
)

REM --- VOICEVOX 応答待機（最大%MAX_WAIT%秒） ---
echo Waiting for VOICEVOX to respond on port 50021...
set WAIT_COUNT=0

:WAIT_LOOP
curl -s -o nul -w "%%{http_code}" "%VOICEVOX_URL%" 2>nul | findstr "200" >nul
if %ERRORLEVEL% EQU 0 (
    echo VOICEVOX is ready. (waited %WAIT_COUNT%s)
    echo [%date% %time%] VOICEVOX ready after %WAIT_COUNT%s >> "%LOG_FILE%"
    goto START_RITSU
)

set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ %MAX_WAIT% (
    echo VOICEVOX not responding after %MAX_WAIT%s. Starting Ritsu anyway...
    echo [%date% %time%] VOICEVOX timeout after %MAX_WAIT%s >> "%LOG_FILE%"
    goto START_RITSU
)

timeout /t 1 /nobreak >nul
goto WAIT_LOOP

REM --- Ritsu 起動 ---
:START_RITSU
echo Starting Ritsu...
echo [%date% %time%] Starting ritsu.py >> "%LOG_FILE%"

start "Ritsu v3" /MIN python "%~dp0ritsu.py"

echo [%date% %time%] Ritsu started >> "%LOG_FILE%"
exit
