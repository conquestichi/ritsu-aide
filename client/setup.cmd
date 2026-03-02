@echo off
echo === 律 v3 セットアップ ===
cd /d "%~dp0"

echo.
echo [1/3] Python パッケージインストール...
pip install -r requirements.txt --break-system-packages 2>nul || pip install -r requirements.txt

echo.
echo [2/3] 設定ファイル準備...
if not exist ".ritsu_worker.env" (
    copy env.v3.example .ritsu_worker.env
    echo .ritsu_worker.env を作成しました。トークンとSSH設定を編集してください。
) else (
    echo .ritsu_worker.env は既に存在します。
)

echo.
echo [3/3] VMC表情マップ確認...
if not exist "vmc_expr_map.json" (
    echo vmc_expr_map.json が見つかりません。旧ディレクトリからコピーしてください。
) else (
    echo vmc_expr_map.json OK
)

echo.
echo === セットアップ完了 ===
echo.
echo 次のステップ:
echo   1. .ritsu_worker.env を編集（RITSU_BEARER_TOKEN, RITSU_SSH_HOST）
echo   2. ritsu.cmd で起動
echo.
pause
