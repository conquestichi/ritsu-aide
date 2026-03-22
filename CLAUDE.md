# 律 Aide V4 — Claude Code ガイド

## プロジェクト概要
Windows完結・1ファイル構成の常駐AIアシスタント「律 (Ritsu)」。
VPS廃止、OpenAI廃止 → Anthropic Claude API + ローカルSTT (faster-whisper)。

## 作業ディレクトリ
- Windows: `C:\Users\conqu\Desktop\ritsu-aide`
- メインファイル: `ritsu_v4.py` (1ファイルに全機能統合)

## アーキテクチャ (V4)
```
ritsu_v4.py
├── Main Thread    → tkinter GUI
├── TTS Thread     → VOICEVOX (localhost:50021)
├── STT Thread     → faster-whisper (ローカル, Phase 2)
├── Monologue      → idle + schedule (Phase 4)
└── Hotkey Thread  → Win32 API (Phase 4)
```

## 重要な設計原則
1. **1ファイル**: ritsu_v4.py に全機能。外部モジュール分割しない
2. **Windows完結**: VPS不要。APIはAnthropic Claudeのみ
3. **設定は.env**: ハードコード禁止
4. **.gitignoreを厳守**: .env, *.sqlite は絶対にcommitしない

## 開発フェーズ
- Phase 1: ✅ 基盤 (GUI + Claude API + TTS)
- Phase 2: STT (faster-whisper + PTT)
- Phase 3: 記憶 (SQLite + 要約 + 知識抽出)
- Phase 4: 独り言 + ホットキー + 自動起動
- Phase 5: 整備 (README, archive整理)

## テスト方法
```powershell
cd C:\Users\conqu\Desktop\ritsu-aide
python ritsu_v4.py
```

## 注意事項
- Win32 hotkey: RegisterHotKey + WH_MOUSE_LL は同一スレッドで
- TTS speaker: style ID直指定 (名前マッチングは文字化けする)
- singleton guard: SO_REUSEADDR 使用禁止
- keyboard ライブラリ使用禁止 (Win32 message pumpと競合)
