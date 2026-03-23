# 律 Aide V4

**Windows完結・API最小・1ファイル構成の常駐AIアシスタント**

律（りつ）は司令官の常駐秘書AI。テキスト/音声で会話、記憶を持ち、独り言を喋り、妹こがねのトレードを報告する。

## 構成

```
ritsu_v4.py (1ファイル, 1470行)
├── Claude API (Anthropic SDK, 頭脳)
├── VOICEVOX TTS (四国めたん あまあま)
├── faster-whisper STT (ローカル推論)
├── SQLite記憶 (会話要約+知識抽出)
├── MonologueThread (定時+アイドル独り言)
├── KoganeWatcher (こがねトレード監視)
├── Win32ホットキー (F10/XButton1/XButton2)
└── tkinter GUI
```

## セットアップ

```bash
git clone https://github.com/conquestichi/ritsu-aide.git
cd ritsu-aide
pip install -r requirements.txt
copy env.example .env   # APIキー記入
python ritsu_v4.py
```

## 操作

| キー | 機能 |
|------|------|
| Enter | テキスト送信 |
| F10 / XButton1 | GUI表示切替 |
| XButton2 (長押し) | PTT音声入力 |
| Esc | GUI非表示 |

## 環境変数

`.env` で全設定。詳細は `env.example` 参照。

## 関連リポジトリ

| リポ | 用途 |
|------|------|
| inga-ritsu-pao | 律 X投稿+YouTube配信 |
| inga-quants | 日本株シグナル生成 |
| inga-quants-hp | 公開サイト ingaquants.jp |
| inga-kogane | 自動売買エンジン |
