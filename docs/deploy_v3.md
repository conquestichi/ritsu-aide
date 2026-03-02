# 律 v3 デプロイ手順

## 1. VPSサーバー更新 (SSH端末で)

```bash
cd /opt/agents/ritsu
sudo cp app.py app.py.bak_$(date +%Y%m%d_%H%M%S)
sudo curl -sL https://raw.githubusercontent.com/conquestichi/ritsu-aide/main/server/app.py -o app.py
sudo chown inga:inga app.py
sudo systemctl restart ritsu.service
sleep 2
curl -fsS http://127.0.0.1:8181/ready
```

期待される出力: `{"status":"ready"}`

## 2. Windows v3 クライアントセットアップ (PowerShellで)

### 2-1. 旧プロセス停止
```powershell
C:\Users\conqu\tts\run_ritsu_worker.cmd stop
# AHKも止める
Get-Process | Where-Object { $_.ProcessName -match "AutoHotkey" } | Stop-Process -Force
```

### 2-2. v3ファイル取得
```powershell
$base = "https://raw.githubusercontent.com/conquestichi/ritsu-aide/main/client"
$dest = "C:\Users\conqu\tts"

# v3 コアファイル
Invoke-WebRequest "$base/ritsu.py" -OutFile "$dest\ritsu.py" -UseBasicParsing
Invoke-WebRequest "$base/ritsu.cmd" -OutFile "$dest\ritsu.cmd" -UseBasicParsing
Invoke-WebRequest "$base/setup.cmd" -OutFile "$dest\setup.cmd" -UseBasicParsing
Invoke-WebRequest "$base/requirements.txt" -OutFile "$dest\requirements.txt" -UseBasicParsing
Invoke-WebRequest "$base/env.v3.example" -OutFile "$dest\env.v3.example" -UseBasicParsing

Write-Host "Done"
```

### 2-3. セットアップ
```powershell
cd C:\Users\conqu\tts
pip install -r requirements.txt
```

### 2-4. 設定ファイル
```powershell
# 既存の.envがあればそのまま使える
# なければ新規作成
if (!(Test-Path "C:\Users\conqu\tts\.ritsu_worker.env")) {
    Copy-Item "C:\Users\conqu\tts\env.v3.example" "C:\Users\conqu\tts\.ritsu_worker.env"
    Write-Host ".ritsu_worker.env を編集してトークンを設定してください"
}
```

`.ritsu_worker.env` に以下を追加（既存の値はそのまま）:
```
RITSU_SSH_HOST=root@160.251.167.44
RITSU_SSH_PORT=22
RITSU_SSH_LOCAL_PORT=18181
RITSU_SSH_REMOTE_PORT=8181
```

### 2-5. 起動
```powershell
cd C:\Users\conqu\tts
python ritsu.py
```

## 3. 動作確認

1. ログウィンドウに `[BOOT] GUI ready` が表示される
2. F10 または マウスXButton1 で小窓表示
3. テキスト入力 → Enter で送信
4. 律が応答 + TTS + VMC表情

## 4. 旧ファイルについて

v3は `ritsu.py` 1ファイルで全機能をカバーするため、
旧ファイル群（ritsu_worker_notify.py, tts_hotkey.ahk, ptt_*.ps1 等）は
不要になります。安定確認後に削除可能。

## アーキテクチャ比較

### 旧 (v1/v2)
```
AHK → ファイル書き出し → CMD → PowerShell → SSH Tunnel → VPS
→ ファイル読み取り → PowerShell TTS → 別プロセス VMC
= 7プロセス、4言語、ファイルIPC
```

### 新 (v3)
```
Python GUI → HTTP POST → VPS → レスポンス → TTS + VMC
= 1プロセス、1言語、インメモリ
```
