@echo off
cd /d "%~dp0"
echo === 律 Aide V4 セットアップ ===
pip install -r requirements.txt
if not exist .env (
    copy env.example .env
    echo .env を作成しました。APIキーを記入してください。
) else (
    echo .env は既に存在します。
)
echo セットアップ完了。
pause
