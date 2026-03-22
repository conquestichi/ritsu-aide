# 律 Aide V4 — System Specification

## Version: 4.0 Draft
## Date: 2026-03-22
## Repository: github.com/conquestichi/ritsu-aide (既存リポを書き直し)

---

## 1. コンセプト

**Windows完結・API最小・1ファイル構成の常駐AIアシスタント**

V3からの変更思想:
- VPS廃止 → 全てWindowsローカルで動作
- OpenAI API廃止 → 頭脳はAnthropic Claude API、耳はローカルSTT
- VMC/OSC表情・リップシンク廃止 → VMagicMirrorは表示のみ
- SSHトンネル・アクションキュー・Bearer認証 → 全廃
- FastAPIサーバー → 不要

---

## 2. アーキテクチャ

```
ritsu_v4.py (1ファイル, Windows完結)
│
├── [Main Thread]     tkinter GUI (テキスト入力・ログ表示)
├── [TTS Thread]      VOICEVOX → sounddevice再生
├── [STT Thread]      faster-whisper (ローカル推論)
├── [Monologue Thread] idle検知 + スケジュール定時発話
└── [Hotkey Thread]   Win32 API (RegisterHotKey + WH_MOUSE_LL)

外部サービス:
├── Anthropic Claude API (HTTPS直接, 頭脳)
├── VOICEVOX (localhost:50021, 音声合成)
└── VMagicMirror (表示のみ, 連携なし)

ローカルデータ:
├── ritsu.sqlite (会話履歴・記憶・要約)
├── .env (APIキー等)
└── monologue_schedule.json (定時発話)
```

---

## 3. 各モジュール仕様

### 3.1 頭脳 — Claude API

**呼出方式**: anthropic Python SDK → Messages API (直接呼出し, VPS不要)

```python
from anthropic import Anthropic
client = Anthropic()  # ANTHROPIC_API_KEY from env
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    system=system_prompt,
    messages=conversation_history,
)
```

**応答形式**: system promptでJSON出力を指示
```json
{
  "reply_text": "応答テキスト",
  "emotion_tag": "calm|happy|sorry|warn|think|neutral"
}
```
- emotion_tagは記憶・ログ・将来の拡張用に残す
- VMagicMirrorへの送信はしない

**会話管理**:
- 直近N往復をmessages配列に含める (デフォルト: 16ターン)
- system promptにペルソナ + 記憶コンテキストを注入

**モデル選択**:
- デフォルト: claude-sonnet-4-20250514 (速度・コスト・品質のバランス)
- 環境変数 RITSU_MODEL で変更可能

### 3.2 声 — VOICEVOX TTS

**変更なし** (V3から継続)
- エンジン: VOICEVOX (localhost:50021)
- 話者: 四国めたん (style ID直指定)
- 出力: sounddevice → スピーカー (EDIFIER MP230)
- VB-CABLE同時出力: オプション (RITSU_TTS_CABLE_DEVICE で設定)

### 3.3 耳 — ローカルSTT (faster-whisper)

**新規実装**: OpenAI Whisper API → faster-whisper (ローカル推論)

```python
from faster_whisper import WhisperModel
model = WhisperModel("small", device="cuda", compute_type="float16")
# or device="cpu" for CPU-only
segments, info = model.transcribe("audio.wav", language="ja")
text = "".join(s.text for s in segments)
```

- モデル: small (約500MB, 初回DL後はオフライン)
- GPU利用: CUDA対応GPU検出時は自動でGPU、なければCPU
- 起動時にモデルロード (数秒), 以降は高速推論
- 環境変数: RITSU_STT_MODEL (デフォルト: small), RITSU_STT_DEVICE (auto/cuda/cpu)

### 3.4 体 — VMagicMirror

**表示のみ** (V3から大幅簡略化)
- VMagicMirror単体で起動・表示
- ritsu.pyからの制御なし (OSC送信なし)
- 表情・リップシンクなし
- 将来的に再接続したくなったら追加可能な設計にはしておく

### 3.5 記憶 — SQLiteローカル

**V3 VPS版から移植・簡略化**

テーブル構成:
```sql
-- 会話ターン
CREATE TABLE turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,          -- user / assistant
    content TEXT NOT NULL,
    emotion_tag TEXT DEFAULT 'neutral',
    ts INTEGER NOT NULL
);

-- 会話要約
CREATE TABLE summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    turn_start INTEGER NOT NULL,
    turn_end INTEGER NOT NULL,
    turn_count INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

-- 知識 (fact/preference/decision/memo)
CREATE TABLE knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',
    confidence REAL NOT NULL DEFAULT 0.8,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- メタデータ
CREATE TABLE memory_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

機能:
- 会話要約: N往復ごとに自動要約 (Claude APIで生成)
- 知識抽出: 会話から自動抽出 (Claude APIで生成)
- 明示記憶: 「覚えて:」「忘れて:」「記憶一覧」コマンド
- system promptに要約+知識を自動注入

### 3.6 GUI — tkinter

**V3から継続 (微調整)**
- 小窓 (480x380)
- テキスト入力 + 送信ボタン
- ログ表示エリア
- 応答時間・emotion_tag表示
- トグル表示/非表示 (F10 or XButton1)

### 3.7 独り言 — MonologueThread

**V3から継続**
- Idle型: 無操作N秒 → Claude APIに独り言プロンプト送信
- Schedule型: monologue_schedule.json の定時発話
- 両モード独立動作

### 3.8 ホットキー — Win32 API

**V3から継続**
- F10: GUI小窓トグル (RegisterHotKey)
- XButton1: GUI小窓トグル (WH_MOUSE_LL hook)
- XButton2: PTT開始/停止 (WH_MOUSE_LL hook)
- フック+GetMessageを同一スレッドで実行

### 3.9 多重起動ガード

**V3から継続**
- ソケットバインド 127.0.0.1:59181 (SO_REUSEADDR なし)
- MessageBox通知

---

## 4. ペルソナ設定

```python
PERSONA = {
    "name": "律",
    "role": "常駐秘書（司令官の実行補助）",
    "call_user": "司令官",
    "tone": "基本は落ち着いたプロ。短く結論から。癒し少し、ツンデレ軽め、たまにドジ要素。",
    "style_rules": [
        "返答フォーマットは『結論→根拠→リスク/反証→次アクション』",
        "質問は最小。仮置きで進める",
        "冗長・重複・ループを避ける",
        "出力は必ず JSON のみ：{ reply_text, emotion_tag }",
    ],
    "emotion_tags": ["calm", "happy", "sorry", "warn", "think", "neutral"],
}
```

---

## 5. 環境変数 (.env)

```env
# --- Anthropic API ---
ANTHROPIC_API_KEY=sk-ant-...
RITSU_MODEL=claude-sonnet-4-20250514

# --- TTS (VOICEVOX) ---
VOICEVOX_URL=http://127.0.0.1:50021
RITSU_TTS_SPEAKER_STYLE_ID=2
RITSU_TTS_CABLE_DEVICE=         # 空=無効, デバイス番号で有効化

# --- STT (faster-whisper) ---
RITSU_STT_MODEL=small
RITSU_STT_DEVICE=auto

# --- GUI ---
RITSU_WINDOW_GEOMETRY=480x380

# --- 独り言 (idle) ---
RITSU_MONOLOGUE_ENABLE=0
RITSU_MONOLOGUE_IDLE_SEC=600
RITSU_MONOLOGUE_COOLDOWN_SEC=900
RITSU_MONOLOGUE_MAX_PER_DAY=20
RITSU_MONOLOGUE_TIME_RANGE=08:00-23:00

# --- 独り言 (schedule) ---
RITSU_MONOLOGUE_SCHEDULE_ENABLE=0
RITSU_MONOLOGUE_SCHEDULE_PATH=monologue_schedule.json
RITSU_MONOLOGUE_SCHEDULE_TOLERANCE_SEC=120

# --- 会話 ---
RITSU_MAX_TURNS=16
RITSU_CONVERSATION_ID=default

# --- 記憶 ---
RITSU_DB_PATH=ritsu.sqlite
RITSU_SUMMARIZE_EVERY=8
RITSU_MAX_KNOWLEDGE=200
```

---

## 6. ファイル構成 (V4)

```
ritsu-aide/
├── ritsu_v4.py              メインクライアント (1ファイル)
├── monologue_schedule.json  独り言スケジュール
├── requirements.txt         依存パッケージ
├── env.example              環境変数テンプレート
├── setup.cmd                初回セットアップ
├── ritsu.cmd                起動スクリプト
├── start_ritsu_autostart.cmd Windows自動起動
├── CLAUDE.md                開発者向け (Claude Code用)
├── README.md                プロジェクト説明
├── docs/
│   └── v4_spec.md           この仕様書
└── archive/                 V3以前のコード (参照用)
    ├── server/              旧VPSサーバー
    ├── client/              旧クライアント
    └── ...
```

旧V3コードは archive/ に移動。ルートはV4のみ。

---

## 7. 依存パッケージ (requirements.txt)

```
anthropic
faster-whisper
sounddevice
numpy
requests
```

---

## 8. V3→V4 削除対象

| 削除するもの | 理由 |
|-------------|------|
| server/ (app.py, ritsu_memory.py, etc.) | VPS廃止、記憶はローカルに移植 |
| SSH Tunnel管理コード | VPS不要 |
| VMC/OSCクライアント (pythonosc) | 表情・リップシンク廃止 |
| リップシンクコード (RMS解析) | 廃止 |
| アクションキュー (actions_queue.py) | VPS廃止 |
| Worker Thread (action polling) | VPS廃止 |
| Bearer Token認証 | VPS廃止 |
| OpenAI SDK (openai) | Claude API + ローカルSTTに移行 |
| winsound / VB-CABLE依存コード | sounddevice統一 (VB-CABLE出力はオプション) |

---

## 9. 開発・デプロイフロー

```
[開発]
Web版 Claude Code → コード修正 → GitHub push

[Windows適用]
PowerShell: cd C:\Users\conqu\tts; git pull

[初回セットアップ]
1. git clone https://github.com/conquestichi/ritsu-aide.git
2. cd ritsu-aide
3. pip install -r requirements.txt
4. copy env.example .env → APIキー記入
5. python ritsu_v4.py

[自動起動]
start_ritsu_autostart.cmd → shell:startup に配置
```

---

## 10. 開発フェーズ

### Phase 1: 基盤 (最小動作)
- [ ] ritsu_v4.py スケルトン (env読込, ログ, 多重起動ガード)
- [ ] Claude API呼出し (テキスト入力→応答)
- [ ] tkinter GUI (テキスト送受信)
- [ ] VOICEVOX TTS (応答読み上げ)

### Phase 2: 音声入力
- [ ] faster-whisper 統合
- [ ] PTT (XButton2) で録音→ローカル文字起こし→応答

### Phase 3: 記憶
- [ ] SQLiteローカルDB (turns, summaries, knowledge)
- [ ] 会話要約 (自動)
- [ ] 知識抽出 (自動)
- [ ] 明示記憶コマンド

### Phase 4: 独り言・ホットキー・自動起動
- [ ] MonologueThread (idle + schedule)
- [ ] ホットキー (F10/XButton1/XButton2)
- [ ] start_ritsu_autostart.cmd 更新

### Phase 5: 整備
- [ ] V3旧ファイルを archive/ に移動
- [ ] README.md 更新
- [ ] env.example 最終版
- [ ] git push → 動作テスト

---

## 11. 設計原則

1. **1ファイル**: ritsu_v4.py に全機能統合。外部モジュール分割しない
2. **Windows完結**: VPS・外部サーバーに依存しない (API呼出しのみ)
3. **API最小**: Anthropic Claude API のみ。STTはローカル
4. **フォールバック**: API障害時も致命的にならない (ログ出力して継続)
5. **設定は.env**: ハードコード禁止。全設定は環境変数から読む
6. **V3互換**: ペルソナ・記憶スキーマ・独り言スケジュールはV3資産を引き継ぐ
