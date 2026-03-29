# CLAUDE.md — 律 Aide V4 開発ガイド

## プロジェクト概要
Windows完結の常駐AIアシスタント。1ファイル構成 (ritsu_v4.py)。

## 関係リポジトリ

| リポ | 短縮名 | 関係 |
|------|--------|------|
| inga-kogane | kogane | 共有知識DB共有、姉妹連携・嫉妬システム、KoganeWatcherがsnapshot/messages APIポーリング |
| inga-quants-hp | hp | 共有知識APIを律LINE botが利用 |
| inga-fact | fact | 朝ブリーフィング連携（/api/fact/today → 律が報告） |
| inga-ritsu-pao | pao | 律のX投稿パイプライン |
| inga-stream | stream | YouTube生配信（streaming_modeフラグ連携予定） |

## アーキテクチャ
- **頭脳**: Anthropic Claude API (anthropic SDK)
- **声**: VOICEVOX TTS (localhost:50021, 四国めたん あまあま style=0)
- **耳**: faster-whisper ローカルSTT (model=small)
- **記憶**: SQLite (turns/summaries/knowledge/memory_meta)
- **独り言**: MonologueThread (Schedule型10スロット + Idle型70%ストック/30%API)
- **こがね監視**: KoganeWatcherThread (kogane-snapshot APIポーリング + LINE会話TTS再生)
- **GUI**: tkinter (Catppucchinカラー)
- **ホットキー**: Win32 API (RegisterHotKey + WH_MOUSE_LL)

## ファイル構成
- `ritsu_v4.py` — メイン (全機能統合, ~1700行)
- `.env` — 設定 (git管理外)
- `ritsu.sqlite` — DB (git管理外)
- `monologue_schedule.json` — 独り言定時スケジュール

## 禁止事項
- `keyboard` ライブラリ使用禁止 (Win32 message pumpと競合)
- `SO_REUSEADDR` 使用禁止 (多重起動ガード用ソケット)
- `.env` / `*.sqlite` の git commit 禁止
- 外部モジュール分割禁止 (1ファイル原則)
- ハードコード禁止 (全設定は環境変数から)

## セクション構成
1. .env loader
2. Configuration
3. Singleton guard
4. SQLite DB (memory)
5. Persona
6. Claude API client + monologue/kogane API
7. VOICEVOX TTS
8. STT (faster-whisper)
9. PTT / Hotkey
9.5. MonologueThread
9.6. KoganeWatcherThread
10. tkinter GUI
11. Main

## 開発フロー
Web版 Claude.ai → コード修正 → git push → Windows側 git pull → python ritsu_v4.py
