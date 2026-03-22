@echo off
:: 律 Aide V4 自動起動スクリプト
:: shell:startup に配置して使用
cd /d "C:\Users\conqu\Desktop\ritsu-aide"

:: VOICEVOX が起動するまで待機
echo Waiting for VOICEVOX...
:wait_voicevox
curl -s http://127.0.0.1:50021/version >nul 2>&1
if errorlevel 1 (
    timeout /t 3 /nobreak >nul
    goto wait_voicevox
)
echo VOICEVOX ready.

:: 律 Aide V4 起動
start "RitsuV4" pythonw ritsu_v4.py
