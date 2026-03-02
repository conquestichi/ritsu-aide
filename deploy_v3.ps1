# deploy_v3.ps1 - 律v3 ワンショットデプロイ
# Usage: powershell -ExecutionPolicy Bypass -File deploy_v3.ps1

$ErrorActionPreference = "Stop"
$dest = "C:\Users\conqu\tts"
$base = "https://raw.githubusercontent.com/conquestichi/ritsu-aide/main/client"

Write-Host "=== 律 v3 デプロイ ===" -ForegroundColor Cyan

# 1. Stop old processes
Write-Host "[1/5] 旧プロセス停止..."
Get-Process | Where-Object {
    $_.ProcessName -match "AutoHotkey" -or
    ($_.CommandLine -and $_.CommandLine -match "ritsu_worker")
} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

# 2. Download v3 files
Write-Host "[2/5] v3ファイルダウンロード..."
$files = @(
    "ritsu.py",
    "ritsu.cmd",
    "setup.cmd",
    "requirements.txt",
    "env.v3.example"
)
foreach ($f in $files) {
    try {
        Invoke-WebRequest "$base/$f" -OutFile "$dest\$f" -UseBasicParsing
        Write-Host "  OK: $f"
    } catch {
        Write-Host "  FAIL: $f - $($_.Exception.Message)" -ForegroundColor Red
    }
}

# 3. Install dependencies
Write-Host "[3/5] Python依存パッケージ..."
& python -m pip install -r "$dest\requirements.txt" --quiet 2>$null
if ($LASTEXITCODE -ne 0) {
    & pip install -r "$dest\requirements.txt" --quiet
}
Write-Host "  OK"

# 4. Config
Write-Host "[4/5] 設定ファイル確認..."
$envFile = "$dest\.ritsu_worker.env"
if (Test-Path $envFile) {
    $content = Get-Content -Raw $envFile
    $needsUpdate = $false

    if ($content -notmatch "RITSU_SSH_HOST") {
        Add-Content $envFile "`n# --- v3 SSH Tunnel ---"
        Add-Content $envFile "RITSU_SSH_HOST=root@160.251.167.44"
        Add-Content $envFile "RITSU_SSH_PORT=22"
        Add-Content $envFile "RITSU_SSH_LOCAL_PORT=18181"
        Add-Content $envFile "RITSU_SSH_REMOTE_PORT=8181"
        Write-Host "  SSH tunnel設定を追加しました"
    } else {
        Write-Host "  設定済み"
    }
} else {
    Copy-Item "$dest\env.v3.example" $envFile
    Write-Host "  新規作成: $envFile"
    Write-Host "  !! RITSU_BEARER_TOKEN を設定してください !!" -ForegroundColor Yellow
}

# 5. Verify
Write-Host "[5/5] 検証..."
$pyCheck = & python -c "import requests, keyboard; print('OK')" 2>&1
if ($pyCheck -match "OK") {
    Write-Host "  依存パッケージ: OK"
} else {
    Write-Host "  依存パッケージに問題あり: $pyCheck" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== デプロイ完了 ===" -ForegroundColor Green
Write-Host ""
Write-Host "起動コマンド:" -ForegroundColor Cyan
Write-Host "  cd $dest"
Write-Host "  python ritsu.py"
Write-Host ""
Write-Host "VPSも更新が必要です（SSH端末で）:" -ForegroundColor Yellow
Write-Host "  cd /opt/agents/ritsu"
Write-Host "  sudo curl -sL https://raw.githubusercontent.com/conquestichi/ritsu-aide/main/server/app.py -o app.py"
Write-Host "  sudo systemctl restart ritsu.service"
