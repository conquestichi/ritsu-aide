# 律 Aide (Ritsu) — V4

Windows完結・API最小・1ファイル構成の常駐AIアシスタント。

## 特徴
- **頭脳**: Anthropic Claude API (SDK直接呼出し)
- **声**: VOICEVOX (ローカルTTS)
- **耳**: faster-whisper (ローカルSTT) *Phase 2*
- **記憶**: SQLite (会話履歴・要約・知識) *Phase 3*
- **体**: VMagicMirror (表示のみ)

## セットアップ
```powershell
cd C:\Users\conqu\Desktop\ritsu-aide
setup.cmd
# .env を編集して ANTHROPIC_API_KEY を設定
```

## 起動
```powershell
python ritsu_v4.py
# or
ritsu.cmd
```

## ファイル構成
```
ritsu_v4.py              メインクライアント (1ファイル)
monologue_schedule.json  独り言スケジュール
requirements.txt         依存パッケージ
env.example              環境変数テンプレート
.env                     ローカル設定 (git管理外)
archive/                 V3以前のコード (参照用)
docs/                    仕様書
```

## 要件
- Python 3.10+
- VOICEVOX Engine (localhost:50021)
- Anthropic API Key

## ライセンス
Private
