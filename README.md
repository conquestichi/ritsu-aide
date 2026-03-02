# ritsu-aide（律）

VPS常駐の秘書AI「律」のソースコード管理リポジトリ。

## アーキテクチャ

```
Windows（Body）          Tailscale閉域網         VPS（Brain）
┌─────────────┐         ┌──────────┐         ┌──────────────────┐
│ AHK 小窓/PTT│────────▶│SSH Tunnel│────────▶│ FastAPI :8181     │
│ VOICEVOX TTS│         │18181→8181│         │ /assistant/text   │
│ VMagicMirror│◀────────│          │◀────────│ /assistant/v2     │
│ Worker(poll)│         └──────────┘         │ /actions/*        │
└─────────────┘                              │ OpenAI API        │
                                             │ SQLite(履歴/キュー)│
                                             │ memory.json(人格) │
                                             └──────────────────┘
```

## ディレクトリ構成

```
server/          VPS側（/opt/agents/ritsu/）
client/
  core/          Worker・Sender・V2クライアント
  tts/           VOICEVOX TTS・Whisper STT
  ui/            AHK ホットキー・PTT
  vmc/           VMagicMirror 表情制御
  boot/          起動・停止スクリプト
config/          設定テンプレート（秘密値なし）
docs/            仕様書・アーキテクチャ図
```

## クイックチェック（Windows）

```powershell
cd C:\Users\conqu\tts
# VPS疎通
try { (iwr http://127.0.0.1:18181/ready -UseBasicParsing -TimeoutSec 3).Content } catch { $_.Exception.Message }
# VOICEVOX
try { (iwr http://127.0.0.1:50021/speakers -UseBasicParsing -TimeoutSec 3).StatusCode } catch { $_.Exception.Message }
```

## セットアップ

1. `client/boot/env.example` をコピーして `.ritsu_worker.env` を作成
2. トークン・APIキーを設定
3. `run_ritsu_worker.cmd start` で起動
