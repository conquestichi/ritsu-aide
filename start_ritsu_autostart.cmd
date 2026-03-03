@echo off
REM ================================================================
REM 律 v3 自動起動スクリプト
REM   1. VOICEVOX 起動 + 応答待機 (最大90秒)
REM   2. ritsu.py 起動 (SSH tunnel + Worker + TTS + VMC 全て内蔵)
REM ================================================================
set RITSU_DIR=C:\Users\conqu\tts
cd /d "%RITSU_DIR%"

REM --- ログ ---
set LOG_DIR=%LOCALAPPDATA%\RitsuWorker
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set LOG_FILE=%LOG_DIR%\autostart_%date:~0,4%%date:~5,2%%date:~8,2%.log

echo [%date% %time%] === Ritsu autostart begin === >> "%LOG_FILE%"

REM ================================================================
REM 1. VOICEVOX 起動
REM ================================================================
set VOICEVOX_EXE=%LOCALAPPDATA%\Programs\VOICEVOX\VOICEVOX.exe
set VOICEVOX_URL=http://127.0.0.1:50021/speakers
set MAX_WAIT=90

REM 既に応答するならスキップ
curl -s -o nul -w "%%{http_code}" "%VOICEVOX_URL%" 2>nul | findstr "200" >nul
if %ERRORLEVEL% EQU 0 (
    echo [OK] VOICEVOX already running.
    echo [%date% %time%] VOICEVOX already running >> "%LOG_FILE%"
    goto START_RITSU
)

REM VOICEVOX起動
if exist "%VOICEVOX_EXE%" (
    echo [..] Starting VOICEVOX...
    echo [%date% %time%] Starting VOICEVOX: %VOICEVOX_EXE% >> "%LOG_FILE%"
    start "" "%VOICEVOX_EXE%"
) else (
    echo [!!] VOICEVOX not found: %VOICEVOX_EXE%
    echo [%date% %time%] VOICEVOX not found: %VOICEVOX_EXE% >> "%LOG_FILE%"
    goto START_RITSU
)

REM --- VOICEVOX 応答待機 ---
echo [..] Waiting for VOICEVOX (max %MAX_WAIT%s)...
set WAIT_COUNT=0

:WAIT_LOOP
curl -s -o nul -w "%%{http_code}" "%VOICEVOX_URL%" 2>nul | findstr "200" >nul
if %ERRORLEVEL% EQU 0 (
    echo [OK] VOICEVOX ready. (waited %WAIT_COUNT%s)
    echo [%date% %time%] VOICEVOX ready after %WAIT_COUNT%s >> "%LOG_FILE%"
    goto START_RITSU
)

set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ %MAX_WAIT% (
    echo [!!] VOICEVOX timeout after %MAX_WAIT%s. Starting Ritsu anyway.
    echo [%date% %time%] VOICEVOX timeout %MAX_WAIT%s >> "%LOG_FILE%"
    goto START_RITSU
)

timeout /t 1 /nobreak >nul
goto WAIT_LOOP

REM ================================================================
REM 2. ritsu.py 起動 (SSHトンネルはritsu.py内蔵管理)
REM ================================================================
:START_RITSU
echo [..] Starting ritsu.py (SSH tunnel managed by ritsu.py)...
echo [%date% %time%] Starting ritsu.py from %RITSU_DIR% >> "%LOG_FILE%"

start "Ritsu v3" /MIN python "%RITSU_DIR%\ritsu.py"

echo [OK] Ritsu started.
echo [%date% %time%] Ritsu started >> "%LOG_FILE%"
exit
