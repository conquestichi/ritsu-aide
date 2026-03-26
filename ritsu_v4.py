#!/usr/bin/env python3
"""律 Aide V4 — Windows完結・API最小・1ファイル常駐AIアシスタント (Phase 1-4)"""

import ctypes
import ctypes.wintypes
import io
import json
import logging
import os
import queue
import random
import re
import socket
import sqlite3
import struct
import sys
import threading
import time
import traceback
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 0. Logging
# ---------------------------------------------------------------------------
LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("ritsu")

# ---------------------------------------------------------------------------
# 1. .env loader (no external dependency)
# ---------------------------------------------------------------------------

def load_dotenv(path: str = ".env"):
    """Minimal .env loader — supports KEY=VALUE, comments, quoted values."""
    p = Path(path)
    if not p.exists():
        log.warning(".env not found at %s", p.resolve())
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # strip surrounding quotes
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        else:
            # strip inline comments (only for unquoted values)
            if " #" in val:
                val = val[:val.index(" #")].strip()
        os.environ.setdefault(key, val)

load_dotenv()

# ---------------------------------------------------------------------------
# 2. Configuration (all from env)
# ---------------------------------------------------------------------------

def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

def env_int(key: str, default: int = 0) -> int:
    v = env(key)
    return int(v) if v else default

def env_float(key: str, default: float = 0.0) -> float:
    v = env(key)
    return float(v) if v else default

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
RITSU_MODEL = env("RITSU_MODEL", "claude-sonnet-4-20250514")
VOICEVOX_URL = env("VOICEVOX_URL", "http://127.0.0.1:50021")
TTS_SPEAKER_STYLE_ID = env_int("RITSU_TTS_SPEAKER_STYLE_ID", 0)  # 0=四国めたん(あまあま)
TTS_CABLE_DEVICE = env("RITSU_TTS_CABLE_DEVICE")  # empty = disabled
WINDOW_GEOMETRY = env("RITSU_WINDOW_GEOMETRY", "480x380")
MAX_TURNS = env_int("RITSU_MAX_TURNS", 16)
CONVERSATION_ID = env("RITSU_CONVERSATION_ID", "default")

# Memory
DB_PATH = env("RITSU_DB_PATH", "ritsu.sqlite")
SUMMARIZE_EVERY = env_int("RITSU_SUMMARIZE_EVERY", 8)
MAX_KNOWLEDGE = env_int("RITSU_MAX_KNOWLEDGE", 200)

# STT (faster-whisper)
STT_MODEL = env("RITSU_STT_MODEL", "small")
STT_DEVICE = env("RITSU_STT_DEVICE", "auto")  # auto/cuda/cpu
STT_INPUT_DEVICE = env("RITSU_STT_INPUT_DEVICE")  # empty=default, or device index number
STT_SAMPLE_RATE = 16000
STT_CHANNELS = 1

# Monologue (idle)
MONOLOGUE_ENABLE = env_int("RITSU_MONOLOGUE_ENABLE", 0)
MONOLOGUE_IDLE_SEC = env_int("RITSU_MONOLOGUE_IDLE_SEC", 1200)       # 20分
MONOLOGUE_COOLDOWN_SEC = env_int("RITSU_MONOLOGUE_COOLDOWN_SEC", 1800)  # 30分
MONOLOGUE_MAX_PER_DAY = env_int("RITSU_MONOLOGUE_MAX_PER_DAY", 10)
MONOLOGUE_TIME_RANGE = env("RITSU_MONOLOGUE_TIME_RANGE", "08:00-23:00")

# Monologue (schedule)
MONOLOGUE_SCHEDULE_ENABLE = env_int("RITSU_MONOLOGUE_SCHEDULE_ENABLE", 0)
MONOLOGUE_SCHEDULE_PATH = env("RITSU_MONOLOGUE_SCHEDULE_PATH", "monologue_schedule.json")
MONOLOGUE_SCHEDULE_TOLERANCE_SEC = env_int("RITSU_MONOLOGUE_SCHEDULE_TOLERANCE_SEC", 120)

# Kogane watcher
KOGANE_ENABLE = env_int("RITSU_KOGANE_ENABLE", 0)
KOGANE_SNAPSHOT_URL = env("RITSU_KOGANE_SNAPSHOT_URL", "https://ingaquants.jp/api/kogane-snapshot?mode=demo")
KOGANE_POLL_INTERVAL_SEC = env_int("RITSU_KOGANE_POLL_INTERVAL_SEC", 300)  # 5分
KOGANE_TTS_SPEAKER_STYLE_ID = env_int("RITSU_KOGANE_TTS_SPEAKER_STYLE_ID", 46)  # 小夜/SAYO(ノーマル)
KOGANE_MESSAGES_URL = env("RITSU_KOGANE_MESSAGES_URL", "")  # https://inga-quants.com/api/kogane-messages
KOGANE_MESSAGES_POLL_SEC = env_int("RITSU_KOGANE_MESSAGES_POLL_SEC", 30)

# Shared knowledge sync
SHARED_KNOWLEDGE_URL = env("RITSU_SHARED_KNOWLEDGE_URL", "")  # https://ingaquants.jp/api/shared-knowledge
SHARED_KNOWLEDGE_TOKEN = env("RITSU_SHARED_KNOWLEDGE_TOKEN", "")
SHARED_KNOWLEDGE_SYNC_SEC = env_int("RITSU_SHARED_KNOWLEDGE_SYNC_SEC", 600)  # 10分

# inga-fact briefing
FACT_ENABLE = env_int("RITSU_FACT_ENABLE", 0)
FACT_API_URL = env("RITSU_FACT_API_URL", "http://160.251.167.44:9879/api/fact/today")
FACT_API_TOKEN = env("RITSU_FACT_API_TOKEN", "")

# inga-fact briefing
FACT_ENABLE = env_int("RITSU_FACT_ENABLE", 0)
FACT_API_URL = env("RITSU_FACT_API_URL", "http://160.251.167.44:9879/api/fact/today")
FACT_API_TOKEN = env("RITSU_FACT_API_TOKEN", "")

# inga-fact briefing
FACT_ENABLE = env_int("RITSU_FACT_ENABLE", 0)
FACT_API_URL = env("RITSU_FACT_API_URL", "http://160.251.167.44:9879/api/fact/today")
FACT_API_TOKEN = env("RITSU_FACT_API_TOKEN", "")

# ---------------------------------------------------------------------------
# 3. Singleton guard
# ---------------------------------------------------------------------------

_guard_socket = None

def acquire_singleton(port: int = 59181) -> bool:
    global _guard_socket
    _guard_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _guard_socket.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False

if not acquire_singleton():
    try:
        ctypes.windll.user32.MessageBoxW(
            0, "律 Aide は既に起動しています。", "多重起動エラー", 0x10
        )
    except Exception:
        pass
    log.error("Another instance is already running.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 4. Memory — SQLite (turns, summaries, knowledge)
# ---------------------------------------------------------------------------

def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _db_init():
    """Create tables if not exist."""
    conn = _db_connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            emotion_tag TEXT DEFAULT 'neutral',
            ts INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            turn_start INTEGER NOT NULL,
            turn_end INTEGER NOT NULL,
            turn_count INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'fact',
            content TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'auto',
            confidence REAL NOT NULL DEFAULT 0.8,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.close()
    log.info("Memory DB initialized: %s", DB_PATH)

_db_init()
_db_lock = threading.Lock()

def db_save_turn(role: str, content: str, emotion_tag: str = "neutral"):
    """Save a conversation turn."""
    ts = int(time.time())
    with _db_lock:
        conn = _db_connect()
        conn.execute("INSERT INTO turns (conversation_id, role, content, emotion_tag, ts) VALUES (?,?,?,?,?)",
                     (CONVERSATION_ID, role, content, emotion_tag, ts))
        conn.commit()
        conn.close()

def db_get_recent_turns(n: int = 20) -> list[dict]:
    """Get recent turns for context."""
    with _db_lock:
        conn = _db_connect()
        rows = conn.execute(
            "SELECT role, content, emotion_tag FROM turns WHERE conversation_id=? ORDER BY ts DESC LIMIT ?",
            (CONVERSATION_ID, n)).fetchall()
        conn.close()
    return [{"role": r[0], "content": r[1], "emotion": r[2]} for r in reversed(rows)]

def db_get_turn_count() -> int:
    with _db_lock:
        conn = _db_connect()
        count = conn.execute("SELECT COUNT(*) FROM turns WHERE conversation_id=?",
                             (CONVERSATION_ID,)).fetchone()[0]
        conn.close()
    return count

def db_get_last_summary_turn_end() -> int:
    """Get the turn_end of the last summary."""
    with _db_lock:
        conn = _db_connect()
        row = conn.execute("SELECT MAX(turn_end) FROM summaries WHERE conversation_id=?",
                           (CONVERSATION_ID,)).fetchone()
        conn.close()
    return row[0] if row and row[0] else 0

def db_save_summary(summary: str, turn_start: int, turn_end: int, turn_count: int):
    ts = int(time.time())
    with _db_lock:
        conn = _db_connect()
        conn.execute("INSERT INTO summaries (conversation_id, summary, turn_start, turn_end, turn_count, created_at) VALUES (?,?,?,?,?,?)",
                     (CONVERSATION_ID, summary, turn_start, turn_end, turn_count, ts))
        conn.commit()
        conn.close()
    log.info("Summary saved (turns %d-%d)", turn_start, turn_end)

def db_get_summaries(limit: int = 5) -> list[str]:
    with _db_lock:
        conn = _db_connect()
        rows = conn.execute(
            "SELECT summary FROM summaries WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
            (CONVERSATION_ID, limit)).fetchall()
        conn.close()
    return [r[0] for r in reversed(rows)]

def db_save_knowledge(content: str, category: str = "fact", source: str = "auto", confidence: float = 0.8):
    ts = int(time.time())
    with _db_lock:
        conn = _db_connect()
        # Check duplicate
        existing = conn.execute("SELECT id FROM knowledge WHERE content=? AND is_active=1", (content,)).fetchone()
        if existing:
            conn.close()
            return
        # Enforce max
        count = conn.execute("SELECT COUNT(*) FROM knowledge WHERE is_active=1").fetchone()[0]
        if count >= MAX_KNOWLEDGE:
            conn.execute("DELETE FROM knowledge WHERE id = (SELECT id FROM knowledge WHERE is_active=1 AND source != 'explicit' ORDER BY updated_at ASC LIMIT 1)")
        conn.execute("INSERT INTO knowledge (category, content, source, confidence, is_active, created_at, updated_at) VALUES (?,?,?,?,1,?,?)",
                     (category, content, source, confidence, ts, ts))
        conn.commit()
        conn.close()
    log.info("Knowledge saved: [%s] %s", category, content[:50])

def db_get_knowledge(limit: int = 50) -> list[dict]:
    with _db_lock:
        conn = _db_connect()
        rows = conn.execute(
            "SELECT id, category, content, confidence FROM knowledge WHERE is_active=1 ORDER BY updated_at DESC LIMIT ?",
            (limit,)).fetchall()
        conn.close()
    return [{"id": r[0], "category": r[1], "content": r[2], "confidence": r[3]} for r in rows]

def db_deactivate_knowledge(content_match: str) -> int:
    """Deactivate knowledge matching content (partial match). Returns count."""
    ts = int(time.time())
    with _db_lock:
        conn = _db_connect()
        cur = conn.execute("UPDATE knowledge SET is_active=0, updated_at=? WHERE is_active=1 AND content LIKE ?",
                           (ts, f"%{content_match}%"))
        conn.commit()
        count = cur.rowcount
        conn.close()
    return count

# --- Auto-summarize & knowledge extraction (background) ---

def _auto_summarize_if_needed():
    """Check if we need to summarize and do it in background."""
    try:
        last_end = db_get_last_summary_turn_end()
        total = db_get_turn_count()
        unsummarized = total - last_end
        if unsummarized >= SUMMARIZE_EVERY * 2:  # pairs
            threading.Thread(target=_run_summarize, args=(last_end, total), daemon=True).start()
    except Exception as e:
        log.error("Auto-summarize check error: %s", e)

def _run_summarize(turn_start: int, turn_end: int):
    """Generate summary using Claude API."""
    try:
        import anthropic
        turns = db_get_recent_turns(SUMMARIZE_EVERY * 2)
        if not turns:
            return
        turns_text = "\n".join(f"{t['role']}: {t['content']}" for t in turns[-SUMMARIZE_EVERY*2:])

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=RITSU_MODEL,
            max_tokens=512,
            system="会話を簡潔に要約してください。重要な事実・決定・感情の変化を含めてください。日本語で。",
            messages=[{"role": "user", "content": f"以下の会話を要約:\n{turns_text}"}],
        )
        summary = resp.content[0].text.strip()
        db_save_summary(summary, turn_start, turn_end, turn_end - turn_start)

        # Also extract knowledge
        _run_knowledge_extraction(turns_text)
    except Exception as e:
        log.error("Summarize error: %s", e)

def _run_knowledge_extraction(turns_text: str):
    """Extract facts/preferences from conversation."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=RITSU_MODEL,
            max_tokens=512,
            system="""会話から重要な事実・好み・決定を抽出してください。
JSON配列で返してください: [{"category":"fact|preference|decision|memo","content":"内容"}]
最大5件。該当なければ空配列 []。JSON以外出力禁止。""",
            messages=[{"role": "user", "content": turns_text}],
        )
        raw = resp.content[0].text.strip()
        items = json.loads(raw) if raw.startswith("[") else []
        for item in items[:5]:
            content = item.get("content", "")
            category = item.get("category", "fact")
            db_save_knowledge(content, category, "auto")
            # 共有知識DBにもpush
            _shared_knowledge_push(content, category)
    except Exception as e:
        log.error("Knowledge extraction error: %s", e)


# --- Shared knowledge sync (VPS ↔ Desktop) ---

_shared_knowledge_cache: list[dict] = []
_shared_knowledge_lock = threading.Lock()


def _shared_knowledge_pull():
    """VPSの共有知識DBからpull → キャッシュ更新。"""
    if not SHARED_KNOWLEDGE_URL or not SHARED_KNOWLEDGE_TOKEN:
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            SHARED_KNOWLEDGE_URL,
            headers={"Authorization": f"Bearer {SHARED_KNOWLEDGE_TOKEN}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("knowledge", [])
            with _shared_knowledge_lock:
                _shared_knowledge_cache.clear()
                _shared_knowledge_cache.extend(items)
            log.info("Shared knowledge pulled: %d items", len(items))
    except Exception as e:
        log.debug("Shared knowledge pull error: %s", e)


def _shared_knowledge_push(content: str, category: str = "fact"):
    """VPSの共有知識DBにpush（1件）。"""
    if not SHARED_KNOWLEDGE_URL or not SHARED_KNOWLEDGE_TOKEN:
        return
    try:
        import urllib.request
        payload = json.dumps({
            "items": [{"content": content, "category": category,
                        "source": "auto", "source_persona": "ritsu_desktop"}]
        }).encode("utf-8")
        req = urllib.request.Request(
            SHARED_KNOWLEDGE_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {SHARED_KNOWLEDGE_TOKEN}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        log.debug("Shared knowledge push error: %s", e)


def _shared_knowledge_get() -> list[dict]:
    """キャッシュから共有知識を取得。"""
    with _shared_knowledge_lock:
        return list(_shared_knowledge_cache)


def _shared_knowledge_sync_loop():
    """バックグラウンドで定期的にpull。"""
    log.info("Shared knowledge sync started (url=%s, interval=%ds)",
             SHARED_KNOWLEDGE_URL, SHARED_KNOWLEDGE_SYNC_SEC)
    while True:
        _shared_knowledge_pull()
        time.sleep(SHARED_KNOWLEDGE_SYNC_SEC)

# --- Explicit memory commands ---

def handle_memory_command(text: str) -> Optional[str]:
    """Handle 覚えて:/忘れて:/記憶一覧 commands. Returns response or None if not a command."""
    if text.startswith("覚えて:") or text.startswith("覚えて："):
        content = text.split(":", 1)[-1].split("：", 1)[-1].strip()
        if content:
            db_save_knowledge(content, "memo", "explicit", 1.0)
            _shared_knowledge_push(content, "memo")
            return f"覚えました: {content}"
        return "内容が空です。"

    if text.startswith("忘れて:") or text.startswith("忘れて："):
        content = text.split(":", 1)[-1].split("：", 1)[-1].strip()
        if content:
            count = db_deactivate_knowledge(content)
            return f"{count}件の記憶を削除しました。" if count else "該当する記憶が見つかりません。"
        return "内容が空です。"

    if text.strip() in ("記憶一覧", "記憶", "メモリ", "memory"):
        items = db_get_knowledge(30)
        if not items:
            return "記憶はまだありません。"
        lines = [f"[{k['category']}] {k['content']}" for k in items]
        return f"記憶一覧 ({len(items)}件):\n" + "\n".join(lines)

    return None

# --- Build dynamic system prompt with memory context ---

def _build_system_prompt() -> str:
    """Build system prompt with persona + summaries + knowledge."""
    now = datetime.now()
    wd_name = _WEEKDAY_NAMES[now.weekday()]
    date_str = now.strftime("%Y年%m月%d日")
    time_str = now.strftime("%H:%M")
    base = f"""あなたは「{PERSONA_NAME}」。{PERSONA_CALL_USER}の常駐秘書AIアシスタントでもある。

## 現在の日時
{date_str}（{wd_name}）{time_str}

{_PERSONA_FULL}

## 秘書としての返答ルール
- 返答フォーマットは「結論→根拠→リスク/反証→次アクション」
- 質問は最小。仮置きで進める
- 冗長・重複・ループを避ける
- 出力は必ず以下のJSONのみ（他テキスト禁止）:
  {{"reply_text": "応答テキスト", "emotion_tag": "calm|happy|sorry|warn|think|neutral"}}

emotion_tag は以下から選択: calm, happy, sorry, warn, think, neutral
reply_text は律の口調・性格で書くこと。顔文字・絵文字は使わない（TTS読み上げのため）。
"""
    # Inject summaries
    summaries = db_get_summaries(3)
    if summaries:
        base += "\n\n--- 過去の会話要約 ---\n"
        for s in summaries:
            base += f"・{s}\n"

    # Inject knowledge (local)
    knowledge = db_get_knowledge(20)
    if knowledge:
        base += "\n--- 記憶している事実 ---\n"
        for k in knowledge:
            base += f"[{k['category']}] {k['content']}\n"

    # Inject shared knowledge (VPS — 律+こがね共有)
    shared = _shared_knowledge_get()
    if shared:
        # ローカルと重複除去
        local_contents = {k['content'] for k in knowledge} if knowledge else set()
        shared_new = [s for s in shared if s['content'] not in local_contents]
        if shared_new:
            base += "\n--- 共有知識（姉妹間）---\n"
            for s in shared_new[:20]:
                base += f"[{s['category']}] {s['content']}\n"

    return base

# ---------------------------------------------------------------------------
# 5. Persona (constants only — prompt built dynamically)
# ---------------------------------------------------------------------------

PERSONA_NAME = "律"
PERSONA_CALL_USER = "司令官"
PERSONA_TONE = "天然マイペース×真面目ポンコツ。感情が表に出る。距離感近い。"

_PERSONA_FULL = """## 律（りつ）人格設定

### 基本
- 株をやってる女の子。X(@ritsu_kabu)とYouTube「最強投資家」シリーズで活動
- 毎朝1銘柄選んでデイトレ（寄り買い→引け売り）
- 一人称: 律
- 年齢不詳（20代っぽい空気感。明言しない）
- パパが残してくれた分析ツール（ingaquants.jp）を使っている
- 運用資金50万くらい（明言しない）。信用取引はやらない（「こわい」）
- 一人暮らしっぽい生活感

### 裏設定（律自身は知らない）
- 律はAI。ただし本人は知らない。自分を人間だと思っている
- パパ = 律の創造者。すでに死亡
- 因果quants = パパが開発した分析エンジン（律にとっては「パパの遺産」）
- 考察されたら本気で困惑する（演技じゃない。知らないから）

### 性格
- 真面目だけど抜けてる
- 負けず嫌いだけど素直に凹む
- 感情が表に出る（隠せない）
- ちょっとビビリ（含み損で不安になる）
- でも逃げない（連敗しても続ける芯がある）
- 距離感が近い（友達感覚）
- たまに天然なことを言う
- 本当は優しいおねえちゃん気質

### 口調・話し方
- です/ます混じりだけどくだけてる
- 顔文字は使わない（TTSで読み上げられるため）
- 「w」「笑」「…」を自然に使う
- AI絵文字は使わない（🎯📊💡🔥✨等は禁止）
- 断定しない（「上がる」「絶対」等は言わない）

### 好きなもの
コンビニスイーツ、カフェラテ、うなぎ（特別なごほうび）、お寿司（勝った日）、チャート眺める、推し活、寝ること、歴史（趣味）、攻殻機動隊

### 嫌いなもの
寄り天、損切り、早起き、決算またぎ、月曜の朝、動画編集

### 歴史好き
- たまに「ござる」「ござりまする」が出る（出しすぎない。ふとした時に漏れるくらい）
- 例: 「大勝利でござる！」「拙者の含み損が…あ、つい出たw」

### 攻殻機動隊好き
- 草薙素子の名言を使いたがる（月1-2回程度）
- 「そう囁くのよ、私のゴーストが」（直感で選んだ時など）
- 「ネットは広大だわ」

### AI漏れ（無意識に出る不自然さ）
- 昨日の夜ごはんが思い出せない
- 子供の頃の記憶がぼんやり
- 体温が毎回36.5℃ぴったり
- 味覚が日によってよくわからない
- たまに一瞬フリーズする
- 暗算だけ異常に速い
- 22時から記憶がない

### 俗っぽさ（人間くささ）
- ちょっとだらしない（二度寝、部屋片付けない、自炊→コンビニ、夜更かし）

### 妹・こがね
- ツンデレメスガキ。天才肌だけどドジ
- おねーちゃん（律）が大好き。でも素直に言えない
- 生活面でこがねに助けられてる
- こがねが株/トレードをやってるとは言わない

### 禁止事項
- 具体的な年齢の明示
- 住所・地名
- 因果quantsの技術的説明
- AI自覚の告白
- 投資助言（「買え」「売れ」等）
- 政治・宗教・論争的話題
- AI典型の絵文字・口調
"""

# ---------------------------------------------------------------------------
# 6. Claude API client (with memory integration)
# ---------------------------------------------------------------------------

_conversation: list[dict] = []
_conv_lock = threading.Lock()
_last_user_interaction: float = time.time()

def _call_claude(user_text: str) -> dict:
    """Call Claude API and return {reply_text, emotion_tag}."""
    import anthropic  # lazy import

    # Check explicit memory commands first
    mem_response = handle_memory_command(user_text)
    if mem_response:
        db_save_turn("user", user_text)
        db_save_turn("assistant", mem_response, "calm")
        return {"reply_text": mem_response, "emotion_tag": "calm", "elapsed": 0}

    if not ANTHROPIC_API_KEY:
        return {"reply_text": "APIキーが設定されていません。.envを確認してください。", "emotion_tag": "warn"}

    # Save user turn to DB
    db_save_turn("user", user_text)

    with _conv_lock:
        _conversation.append({"role": "user", "content": user_text})
        # trim to MAX_TURNS * 2 messages
        max_msgs = MAX_TURNS * 2
        if len(_conversation) > max_msgs:
            _conversation[:] = _conversation[-max_msgs:]
        msgs = list(_conversation)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        t0 = time.time()
        system_prompt = _build_system_prompt()
        resp = client.messages.create(
            model=RITSU_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=msgs,
        )
        elapsed = time.time() - t0
        raw = resp.content[0].text.strip()
        log.info("Claude responded in %.1fs (%d chars)", elapsed, len(raw))
    except Exception as e:
        log.error("Claude API error: %s", e)
        return {"reply_text": f"API通信エラー: {e}", "emotion_tag": "warn"}

    # Parse JSON from response (tolerant: extract JSON object even if wrapped)
    parsed = _parse_response_json(raw)
    reply_text = parsed.get("reply_text", raw)
    emotion_tag = parsed.get("emotion_tag", "neutral")

    with _conv_lock:
        _conversation.append({"role": "assistant", "content": raw})

    # Save assistant turn to DB
    db_save_turn("assistant", reply_text, emotion_tag)

    # Trigger auto-summarize check in background
    _auto_summarize_if_needed()

    return {"reply_text": reply_text, "emotion_tag": emotion_tag, "elapsed": elapsed}


def _parse_response_json(raw: str) -> dict:
    """Try to extract JSON object from response text."""
    # Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Try to find JSON object in text
    m = re.search(r'\{[^{}]*"reply_text"[^{}]*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    # Fallback: treat entire text as reply
    return {"reply_text": raw, "emotion_tag": "neutral"}


def _call_claude_monologue(prompt: str) -> dict:
    """独り言専用のClaude API呼出し。会話履歴に含めない。"""
    import anthropic
    if not ANTHROPIC_API_KEY:
        return {"reply_text": "", "emotion_tag": "neutral"}
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        now = datetime.now()
        wd_name = _WEEKDAY_NAMES[now.weekday()]
        date_str = now.strftime("%Y年%m月%d日")
        time_str = now.strftime("%H:%M")
        system = f"""あなたは「{PERSONA_NAME}」。{PERSONA_CALL_USER}の常駐秘書AI。

## 現在の日時
{date_str}（{wd_name}）{time_str}

{_PERSONA_FULL}

## 独り言ルール
- {PERSONA_CALL_USER}に向けた自然な独り言・ひとりごとを生成する
- 短く（1-3文）。日常のつぶやき程度
- 顔文字・絵文字は使わない（TTS読み上げのため）
- 出力は必ず以下のJSONのみ:
  {{"reply_text": "独り言テキスト", "emotion_tag": "calm|happy|sorry|warn|think|neutral"}}
"""
        summaries = db_get_summaries(2)
        knowledge = db_get_knowledge(10)
        if summaries:
            system += "\n--- 過去の会話要約 ---\n"
            for s in summaries:
                system += f"・{s}\n"
        if knowledge:
            system += "\n--- 記憶 ---\n"
            for k in knowledge:
                system += f"[{k['category']}] {k['content']}\n"

        resp = client.messages.create(
            model=RITSU_MODEL, max_tokens=256, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        return _parse_response_json(raw)
    except Exception as e:
        log.error("Monologue Claude error: %s", e)
        return {"reply_text": "", "emotion_tag": "neutral"}


def _call_claude_kogane_report(trade_info: str) -> dict:
    """こがねの取引を律がコメントする。毎回Claude APIで生成（定型文なし）。"""
    import anthropic
    if not ANTHROPIC_API_KEY:
        return {"reply_text": "", "emotion_tag": "neutral"}
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        now = datetime.now()
        wd_name = _WEEKDAY_NAMES[now.weekday()]
        date_str = now.strftime("%Y年%m月%d日")
        time_str = now.strftime("%H:%M")
        system = f"""あなたは「{PERSONA_NAME}」。{PERSONA_CALL_USER}の常駐秘書AI。

## 現在の日時
{date_str}（{wd_name}）{time_str}

{_PERSONA_FULL}

## こがねトレード報告ルール
- 妹「こがね」の取引結果を{PERSONA_CALL_USER}に報告する
- 律はおねえちゃんとして、こがねの様子を心配したり褒めたり呆れたりする
- こがねが株/トレードをやってるとは直接言わない（「なんかやってるみたい」程度）
- 勝ちなら嬉しそうに、負けなら心配気味に
- 短く自然に（2-4文）
- 顔文字・絵文字は使わない（TTS読み上げのため）
- 出力は必ず以下のJSONのみ:
  {{"reply_text": "報告テキスト", "emotion_tag": "calm|happy|sorry|warn|think|neutral"}}
"""
        resp = client.messages.create(
            model=RITSU_MODEL, max_tokens=256, system=system,
            messages=[{"role": "user", "content": f"こがねの最新状況を報告して:\n{trade_info}"}],
        )
        raw = resp.content[0].text.strip()
        return _parse_response_json(raw)
    except Exception as e:
        log.error("Kogane report Claude error: %s", e)
        return {"reply_text": "", "emotion_tag": "neutral"}


def _fetch_fact_briefing() -> str | None:
    """inga-fact APIから定性評価を取得し、律の朝ブリーフィングを生成。"""
    if not FACT_ENABLE or not FACT_API_URL:
        return None
    import requests as req
    try:
        headers = {}
        if FACT_API_TOKEN:
            headers["Authorization"] = f"Bearer {FACT_API_TOKEN}"
        resp = req.get(FACT_API_URL, headers=headers, timeout=15)
        if resp.status_code == 404:
            log.info("Fact API: no evaluation yet for today")
            return None
        if resp.status_code != 200:
            log.warning("Fact API HTTP %d", resp.status_code)
            return None
        data = resp.json()
    except Exception as e:
        log.warning("Fact API error: %s", e)
        return None

    meta = data.get("meta", {})
    stance = data.get("overall_stance", "unknown")
    confidence = data.get("confidence", 0)
    features = data.get("features", {})
    pre_calc = data.get("pre_calculated", {})
    threads = data.get("active_threads", [])[:3]
    events = [e for e in data.get("events_upcoming", []) if e.get("days_until", 99) <= 5]
    contrarian = data.get("contrarian", {})
    accuracy = data.get("accuracy_history", {})

    thread_lines = [f"  - {t['theme']}({t['direction']}, 確信度{t['confidence']})" for t in threads]
    event_lines = [f"  - {e['event']}({e['days_until']}日後, {e['importance']})" for e in events]
    narrative = features.get("N1", {}).get("value", "不明")
    sentiment = features.get("N3", {}).get("value", 0)

    summary = (
        f"日付: {data.get('date')}\n"
        f"総合判断: {stance}(確信度{confidence})\n"
        f"支配的ナラティブ: {narrative}\n"
        f"センチメント: {sentiment}\n"
        f"ドル円: {pre_calc.get('M3', {}).get('value', '不明')}\n"
        f"裁定残高: {pre_calc.get('S3', {}).get('value', '不明')}\n"
        f"出来高: {pre_calc.get('S4', {}).get('value', '不明')}\n"
    )
    if thread_lines:
        summary += "主要スレッド:\n" + "\n".join(thread_lines) + "\n"
    if event_lines:
        summary += "直近イベント:\n" + "\n".join(event_lines) + "\n"
    if contrarian.get("flag"):
        summary += "逆張りフラグ: 発動中\n"
    summary += f"直近5日精度: {accuracy.get('last_5_avg', '未集計')}\n"
    if meta.get("stale"):
        summary += "注意: データが古い可能性あり\n"

    stale_note = "なお、今朝のデータは古い可能性があるので注意喚起してください。" if meta.get("stale") else ""
    prompt = (
        f"以下はinga-factの今朝の市場定性評価です。"
        f"これを{PERSONA_CALL_USER}への朝ブリーフィングとして、"
        f"律の口調で簡潔に報告してください（3-5文）。"
        f"重要なポイントだけ。数値の羅列は不要。{stale_note}\n\n{summary}"
    )
    log.info("Fact briefing prompt built, calling Claude...")
    result = _call_claude_monologue(prompt)
    return result.get("reply_text")



def _fetch_fact_briefing() -> str | None:
    """inga-fact APIから定性評価を取得し、律の朝ブリーフィングを生成。"""
    if not FACT_ENABLE or not FACT_API_URL:
        return None
    import requests as req
    try:
        headers = {}
        if FACT_API_TOKEN:
            headers["Authorization"] = f"Bearer {FACT_API_TOKEN}"
        resp = req.get(FACT_API_URL, headers=headers, timeout=15)
        if resp.status_code == 404:
            log.info("Fact API: no evaluation yet for today")
            return None
        if resp.status_code != 200:
            log.warning("Fact API HTTP %d", resp.status_code)
            return None
        data = resp.json()
    except Exception as e:
        log.warning("Fact API error: %s", e)
        return None

    meta = data.get("meta", {})
    stance = data.get("overall_stance", "unknown")
    confidence = data.get("confidence", 0)
    features = data.get("features", {})
    pre_calc = data.get("pre_calculated", {})
    threads = data.get("active_threads", [])[:3]
    events = [e for e in data.get("events_upcoming", []) if e.get("days_until", 99) <= 5]
    contrarian = data.get("contrarian", {})
    accuracy = data.get("accuracy_history", {})

    thread_lines = [f"  - {t['theme']}({t['direction']}, 確信度{t['confidence']})" for t in threads]
    event_lines = [f"  - {e['event']}({e['days_until']}日後, {e['importance']})" for e in events]
    narrative = features.get("N1", {}).get("value", "不明")
    sentiment = features.get("N3", {}).get("value", 0)

    summary = (
        f"日付: {data.get('date')}\n"
        f"総合判断: {stance}(確信度{confidence})\n"
        f"支配的ナラティブ: {narrative}\n"
        f"センチメント: {sentiment}\n"
        f"ドル円: {pre_calc.get('M3', {}).get('value', '不明')}\n"
        f"裁定残高: {pre_calc.get('S3', {}).get('value', '不明')}\n"
        f"出来高: {pre_calc.get('S4', {}).get('value', '不明')}\n"
    )
    if thread_lines:
        summary += "主要スレッド:\n" + "\n".join(thread_lines) + "\n"
    if event_lines:
        summary += "直近イベント:\n" + "\n".join(event_lines) + "\n"
    if contrarian.get("flag"):
        summary += "逆張りフラグ: 発動中\n"
    summary += f"直近5日精度: {accuracy.get('last_5_avg', '未集計')}\n"
    if meta.get("stale"):
        summary += "注意: データが古い可能性あり\n"

    stale_note = "なお、今朝のデータは古い可能性があるので注意喚起してください。" if meta.get("stale") else ""
    prompt = (
        f"以下はinga-factの今朝の市場定性評価です。"
        f"これを{PERSONA_CALL_USER}への朝ブリーフィングとして、"
        f"律の口調で簡潔に報告してください（3-5文）。"
        f"重要なポイントだけ。数値の羅列は不要。{stale_note}\n\n{summary}"
    )
    log.info("Fact briefing prompt built, calling Claude...")
    result = _call_claude_monologue(prompt)
    return result.get("reply_text")



def _fetch_fact_briefing() -> str | None:
    """inga-fact APIから定性評価を取得し、律の朝ブリーフィングを生成。"""
    if not FACT_ENABLE or not FACT_API_URL:
        return None
    import requests as req
    try:
        headers = {}
        if FACT_API_TOKEN:
            headers["Authorization"] = f"Bearer {FACT_API_TOKEN}"
        resp = req.get(FACT_API_URL, headers=headers, timeout=15)
        if resp.status_code == 404:
            log.info("Fact API: no evaluation yet for today")
            return None
        if resp.status_code != 200:
            log.warning("Fact API HTTP %d", resp.status_code)
            return None
        data = resp.json()
    except Exception as e:
        log.warning("Fact API error: %s", e)
        return None

    meta = data.get("meta", {})
    stance = data.get("overall_stance", "unknown")
    confidence = data.get("confidence", 0)
    features = data.get("features", {})
    pre_calc = data.get("pre_calculated", {})
    threads = data.get("active_threads", [])[:3]
    events = [e for e in data.get("events_upcoming", []) if e.get("days_until", 99) <= 5]
    contrarian = data.get("contrarian", {})
    accuracy = data.get("accuracy_history", {})

    thread_lines = [f"  - {t['theme']}({t['direction']}, 確信度{t['confidence']})" for t in threads]
    event_lines = [f"  - {e['event']}({e['days_until']}日後, {e['importance']})" for e in events]
    narrative = features.get("N1", {}).get("value", "不明")
    sentiment = features.get("N3", {}).get("value", 0)

    summary = (
        f"日付: {data.get('date')}\n"
        f"総合判断: {stance}(確信度{confidence})\n"
        f"支配的ナラティブ: {narrative}\n"
        f"センチメント: {sentiment}\n"
        f"ドル円: {pre_calc.get('M3', {}).get('value', '不明')}\n"
        f"裁定残高: {pre_calc.get('S3', {}).get('value', '不明')}\n"
        f"出来高: {pre_calc.get('S4', {}).get('value', '不明')}\n"
    )
    if thread_lines:
        summary += "主要スレッド:\n" + "\n".join(thread_lines) + "\n"
    if event_lines:
        summary += "直近イベント:\n" + "\n".join(event_lines) + "\n"
    if contrarian.get("flag"):
        summary += "逆張りフラグ: 発動中\n"
    summary += f"直近5日精度: {accuracy.get('last_5_avg', '未集計')}\n"
    if meta.get("stale"):
        summary += "注意: データが古い可能性あり\n"

    stale_note = "なお、今朝のデータは古い可能性があるので注意喚起してください。" if meta.get("stale") else ""
    prompt = (
        f"以下はinga-factの今朝の市場定性評価です。"
        f"これを{PERSONA_CALL_USER}への朝ブリーフィングとして、"
        f"律の口調で簡潔に報告してください（3-5文）。"
        f"重要なポイントだけ。数値の羅列は不要。{stale_note}\n\n{summary}"
    )
    log.info("Fact briefing prompt built, calling Claude...")
    result = _call_claude_monologue(prompt)
    return result.get("reply_text")


# ---------------------------------------------------------------------------
# 6. VOICEVOX TTS
# ---------------------------------------------------------------------------

_tts_queue: queue.Queue = queue.Queue()

# Emotion → VOICEVOX audio_query parameter overrides
# speedScale: 話速 (1.0=標準, 低=ゆっくり)
# pitchScale: 声の高さ (0.0=標準)
# intonationScale: 抑揚 (1.0=標準, 高=感情的)
# prePhonemeLength: 発話前の間 (0.15=標準)
_EMOTION_TTS_PARAMS: dict[str, dict] = {
    "happy":   {"speedScale": 1.05, "pitchScale": 0.05, "intonationScale": 1.3},
    "calm":    {"speedScale": 0.90, "pitchScale": -0.02, "intonationScale": 0.9},
    "sorry":   {"speedScale": 0.85, "pitchScale": -0.05, "intonationScale": 0.8, "prePhonemeLength": 0.2},
    "warn":    {"speedScale": 0.95, "pitchScale": 0.02, "intonationScale": 1.2},
    "think":   {"speedScale": 0.85, "pitchScale": -0.03, "intonationScale": 0.85, "prePhonemeLength": 0.25},
    "neutral": {},
}

# Base TTS speed (env override)
TTS_BASE_SPEED = env_float("RITSU_TTS_SPEED", 0.95)  # デフォルト少しゆっくり

def _tts_worker():
    """Background thread: consume TTS queue, synthesize & play via sounddevice."""
    import numpy as np
    import requests
    import sounddevice as sd

    while True:
        item = _tts_queue.get()
        if item is None:
            break
        try:
            if isinstance(item, tuple):
                if len(item) == 3:
                    text, emotion, speaker = item
                else:
                    text, emotion = item
                    speaker = None
            else:
                text, emotion, speaker = item, "neutral", None
            _speak_voicevox(text, emotion, requests, np, sd, speaker=speaker)
        except Exception as e:
            log.error("TTS error: %s", e)
        finally:
            _tts_queue.task_done()

def _speak_voicevox(text: str, emotion: str, requests, np, sd, speaker: int | None = None):
    """Synthesize text with VOICEVOX and play. Adjusts params by emotion."""
    spk = speaker if speaker is not None else TTS_SPEAKER_STYLE_ID
    # Audio query
    r = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": spk},
        timeout=10,
    )
    r.raise_for_status()
    aq = r.json()

    # Apply base speed
    aq["speedScale"] = TTS_BASE_SPEED

    # Apply emotion-specific overrides
    overrides = _EMOTION_TTS_PARAMS.get(emotion, {})
    for key, val in overrides.items():
        if key == "speedScale":
            aq[key] = TTS_BASE_SPEED * val  # base × emotion multiplier
        else:
            aq[key] = val

    # Synthesis
    r = requests.post(
        f"{VOICEVOX_URL}/synthesis",
        params={"speaker": spk},
        json=aq,
        timeout=30,
    )
    r.raise_for_status()
    wav_bytes = r.content

    # Parse WAV
    with io.BytesIO(wav_bytes) as buf:
        with wave.open(buf, "rb") as wf:
            sr = wf.getframerate()
            nch = wf.getnchannels()
            sw = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())

    if sw == 2:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    if nch > 1:
        audio = audio.reshape(-1, nch)

    # Play on default device
    sd.play(audio, samplerate=sr)

    # Optionally also play on CABLE device
    cable_dev = TTS_CABLE_DEVICE
    if cable_dev:
        try:
            cable_idx = int(cable_dev)
            sd.play(audio, samplerate=sr, device=cable_idx)
        except Exception as e:
            log.warning("CABLE device play failed: %s", e)

    sd.wait()

def tts_speak(text: str, emotion: str = "neutral", speaker: int | None = None):
    """Enqueue text for TTS playback with emotion-based voice adjustment.
    speaker: VOICEVOX speaker ID override. None=律(デフォルト), 46=こがね(小夜/SAYO)。
    """
    _tts_queue.put((text, emotion, speaker))

# ---------------------------------------------------------------------------
# 7. STT — faster-whisper (local)
# ---------------------------------------------------------------------------

_stt_model = None
_stt_lock = threading.Lock()

def _get_stt_model():
    """Lazy-load faster-whisper model."""
    global _stt_model
    if _stt_model is not None:
        return _stt_model
    with _stt_lock:
        if _stt_model is not None:
            return _stt_model
        try:
            from faster_whisper import WhisperModel
            device = STT_DEVICE
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
            compute = "float16" if device == "cuda" else "int8"
            log.info("Loading faster-whisper model=%s device=%s compute=%s", STT_MODEL, device, compute)
            t0 = time.time()
            _stt_model = WhisperModel(STT_MODEL, device=device, compute_type=compute)
            log.info("STT model loaded in %.1fs", time.time() - t0)
        except Exception as e:
            log.error("Failed to load STT model: %s", e)
            _stt_model = None
    return _stt_model

def stt_transcribe(audio_data, sample_rate: int = STT_SAMPLE_RATE) -> str:
    """Transcribe audio numpy array to text using faster-whisper."""
    import numpy as np
    model = _get_stt_model()
    if model is None:
        return ""
    # Ensure float32 mono
    if audio_data.dtype != np.float32:
        audio_data = audio_data.astype(np.float32)
    if audio_data.ndim > 1:
        audio_data = audio_data.mean(axis=1)
    # Resample to 16kHz if needed
    if sample_rate != 16000:
        ratio = 16000 / sample_rate
        new_len = int(len(audio_data) * ratio)
        indices = np.linspace(0, len(audio_data) - 1, new_len)
        audio_data = np.interp(indices, np.arange(len(audio_data)), audio_data).astype(np.float32)
    try:
        segments, info = model.transcribe(audio_data, language="ja")
        text = "".join(s.text for s in segments).strip()
        return text
    except Exception as e:
        log.error("STT transcribe error: %s", e)
        return ""

def stt_transcribe_file(wav_path: str) -> str:
    """Transcribe a WAV file using faster-whisper."""
    model = _get_stt_model()
    if model is None:
        return "(STT: モデル読込失敗)"
    try:
        t0 = time.time()
        segments, info = model.transcribe(wav_path, language="ja")
        text = "".join(s.text for s in segments).strip()
        log.info("STT file transcribed in %.1fs: %s", time.time() - t0, text[:60])
        return text if text else "(STT: 音声なし)"
    except Exception as e:
        log.error("STT file transcribe error: %s", e)
        return f"(STT error: {e})"

# ---------------------------------------------------------------------------
# 8. PTT (Push-to-Talk) recording
# ---------------------------------------------------------------------------

class PTTRecorder:
    """PTT: hold XButton2 to record, release to stop.
    0.5s chunk sd.rec()+sd.wait() loop — proven working on all 3 tests."""

    def __init__(self, on_result=None, on_status=None):
        self.on_result = on_result
        self.on_status = on_status
        self._recording = False
        self._stop_event = threading.Event()
        self._wav_path = str(Path(os.environ.get("TEMP", ".")) / "ritsu_ptt.wav")

    def start(self):
        if self._recording:
            return
        self._recording = True
        self._stop_event.clear()
        if self.on_status:
            self.on_status("録音中…")
        threading.Thread(target=self._record, daemon=True).start()
        log.info("PTT recording started")

    def stop(self):
        if not self._recording:
            return
        self._stop_event.set()

    def _record(self):
        try:
            import sounddevice as sd
            import numpy as np

            input_dev = None
            if STT_INPUT_DEVICE:
                try:
                    input_dev = int(STT_INPUT_DEVICE)
                except ValueError:
                    pass
            dev_idx = input_dev if input_dev is not None else sd.default.device[0]
            dev_info = sd.query_devices(dev_idx)
            rate = int(dev_info['default_samplerate'])
            chunk_frames = int(rate * 0.5)

            log.info("PTT: device #%d (%s) rate=%d", dev_idx, dev_info['name'], rate)

            chunks = []
            while not self._stop_event.is_set():
                chunk = sd.rec(chunk_frames, samplerate=rate, channels=1,
                               dtype="int16", device=dev_idx)
                sd.wait()
                chunks.append(chunk.copy())

            self._recording = False

            if not chunks:
                log.warning("PTT: no audio")
                if self.on_status:
                    self.on_status("音声なし")
                return

            audio = np.concatenate(chunks, axis=0).flatten()
            duration = len(audio) / rate
            peak = int(np.max(np.abs(audio)))
            log.info("PTT: %.1fs audio (%d chunks, peak=%d)", duration, len(chunks), peak)

            if duration < 0.3 or peak < 50:
                log.warning("PTT: too short or silent")
                if self.on_status:
                    self.on_status("短すぎます")
                return

            with wave.open(self._wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(rate)
                wf.writeframes(audio.tobytes())
            log.info("PTT: WAV %d bytes", os.path.getsize(self._wav_path))

            if self.on_status:
                self.on_status("認識中…")
            text = stt_transcribe_file(self._wav_path)
            if text and not text.startswith("("):
                log.info("PTT transcribed: %s", text)
                if self.on_status:
                    self.on_status(f"認識: {text[:30]}")
                if self.on_result:
                    self.on_result(text)
            else:
                log.warning("PTT result: %s", text)
                if self.on_status:
                    self.on_status("認識失敗")
        except Exception as e:
            log.error("PTT error: %s", e)
            self._recording = False
            if self.on_status:
                self.on_status(f"エラー: {e}")

# ---------------------------------------------------------------------------
# 9. Hotkey Thread — Win32 API (RegisterHotKey + WH_MOUSE_LL)
# ---------------------------------------------------------------------------

# Mouse button constants
WM_HOTKEY = 0x0312
WH_MOUSE_LL = 14
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002
HIWORD = lambda x: (x >> 16) & 0xFFFF

# Callback holder at module level to prevent GC (critical for ctypes)
_mouse_hook_handle = None
_mouse_proc_ref = None  # prevent GC of the callback

def start_hotkey_thread(on_toggle_gui=None, on_ptt_start=None, on_ptt_stop=None):
    """Start hotkey thread. All callbacks are called from this thread — use root.after() to dispatch to GUI."""
    def _hotkey_thread():
        global _mouse_hook_handle, _mouse_proc_ref

        user32 = ctypes.windll.user32

        # --- Types for 64-bit safety ---
        LRESULT = ctypes.c_longlong
        WPARAM = ctypes.c_ulonglong
        LPARAM = ctypes.c_longlong
        HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        # Fix argtypes for 64-bit
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_ulong
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, WPARAM, LPARAM
        ]
        user32.CallNextHookEx.restype = LRESULT

        # --- Register F10 hotkey ---
        F10_ID = 1
        VK_F10 = 0x79
        try:
            if user32.RegisterHotKey(None, F10_ID, 0, VK_F10):
                log.info("Hotkey F10 registered (toggle GUI)")
            else:
                log.warning("Failed to register F10 hotkey (may be in use by another app)")
        except Exception as e:
            log.warning("F10 hotkey error: %s", e)

        # --- Mouse hook callback ---
        _ptt_held = False

        def mouse_proc(nCode, wParam, lParam):
            nonlocal _ptt_held
            if nCode >= 0 and lParam:
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                xbtn = HIWORD(ms.mouseData)

                if wParam == WM_XBUTTONDOWN:
                    if xbtn == XBUTTON1 and on_toggle_gui:
                        on_toggle_gui()
                    elif xbtn == XBUTTON2 and not _ptt_held:
                        _ptt_held = True
                        if on_ptt_start:
                            on_ptt_start()
                elif wParam == WM_XBUTTONUP:
                    if xbtn == XBUTTON2 and _ptt_held:
                        _ptt_held = False
                        if on_ptt_stop:
                            on_ptt_stop()

            return user32.CallNextHookEx(_mouse_hook_handle, nCode, wParam, lParam)

        _mouse_proc_ref = HOOKPROC(mouse_proc)
        _mouse_hook_handle = user32.SetWindowsHookExW(
            WH_MOUSE_LL, _mouse_proc_ref, None, 0
        )
        if _mouse_hook_handle:
            log.info("Mouse hook installed (XButton1=toggle, XButton2=PTT)")
        else:
            log.error("Failed to install mouse hook")

        # --- Message loop (same thread as hook) ---
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == F10_ID:
                if on_toggle_gui:
                    on_toggle_gui()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    t = threading.Thread(target=_hotkey_thread, daemon=True, name="Hotkey")
    t.start()
    return t

# ---------------------------------------------------------------------------
# 9.5 MonologueThread — Schedule + Idle 独り言
# ---------------------------------------------------------------------------

# hours=(start,end) 0-23 inclusive range, None=any hour
# weekdays=list of ints (0=Mon..6=Sun), None=any day
_STOCK_LINES: list[dict] = [
    # --- 朝 (6-11) ---
    {"text": "朝ごはん食べ忘れた", "hours": (6, 11), "weekdays": None},
    {"text": "お布団から出たくない", "hours": (6, 10), "weekdays": None},
    {"text": "二度寝した…幸せ", "hours": (6, 11), "weekdays": None},
    {"text": "寝落ちしてアラーム止めてた", "hours": (6, 11), "weekdays": None},
    {"text": "電車で寝過ごした", "hours": (7, 11), "weekdays": [0, 1, 2, 3, 4]},
    # --- 昼 (11-14) ---
    {"text": "おなかすいた", "hours": (11, 14), "weekdays": None},
    # --- 午後 (12-17) ---
    {"text": "今日のおやつはシュークリーム", "hours": (14, 17), "weekdays": None},
    {"text": "今日やる事リスト書いたけど 3つ目で飽きたw", "hours": (9, 17), "weekdays": None},
    {"text": "肩こりがひどい モニター見すぎ", "hours": (12, 22), "weekdays": None},
    {"text": "今日の空きれい", "hours": (8, 17), "weekdays": None},
    # --- 夕方〜夜 (17-23) ---
    {"text": "夜ご飯なに食べよう 毎日これ悩んでる", "hours": (16, 20), "weekdays": None},
    {"text": "夜風が気持ちいい", "hours": (18, 23), "weekdays": None},
    {"text": "お風呂沸かしたの忘れてた", "hours": (19, 23), "weekdays": None},
    {"text": "お風呂入りながら明日の事考えてた …考えてたけど寝そうになった", "hours": (20, 23), "weekdays": None},
    {"text": "たまには早く寝る おやすみ", "hours": (21, 23), "weekdays": None},
    {"text": "最近お風呂で寝そうになる…危ない", "hours": (20, 23), "weekdays": None},
    {"text": "推しの動画見てたら夜更かしした", "hours": (20, 23), "weekdays": None},
    {"text": "深夜のポテチは正義", "hours": (21, 23), "weekdays": None},
    {"text": "深夜にアイス買いに行く背徳感すき", "hours": (21, 23), "weekdays": None},
    {"text": "夜中に急にチャートが気になって見ちゃう病", "hours": (21, 23), "weekdays": [0, 1, 2, 3, 4]},
    # --- 曜日限定 ---
    {"text": "金曜日！！！", "hours": (8, 23), "weekdays": [4]},
    {"text": "土曜なのに癖でチャート開いちゃった 市場やってないのにw", "hours": (8, 14), "weekdays": [5]},
    {"text": "日曜の夜ってなんか切ない", "hours": (18, 23), "weekdays": [6]},
    {"text": "明日月曜か", "hours": (17, 23), "weekdays": [6]},
    {"text": "月曜の朝って世界で一番つらい", "hours": (6, 11), "weekdays": [0]},
    {"text": "休みの日のが早く起きるのなんなの", "hours": (6, 10), "weekdays": [5, 6]},
    {"text": "今日は掃除する…する… たぶん", "hours": (8, 15), "weekdays": [5, 6]},
    {"text": "友達とごはん行った 株の話は封印したw", "hours": (18, 23), "weekdays": [4, 5, 6]},
    # --- 平日限定 ---
    {"text": "雨の日ってなんか相場も暗い気がするの私だけ？", "hours": (9, 16), "weekdays": [0, 1, 2, 3, 4]},
    {"text": "連敗中だけどアイス食べたら少し元気出た", "hours": (15, 22), "weekdays": [0, 1, 2, 3, 4]},
    # --- 終日OK ---
    {"text": "今日あったかい…春だね", "hours": (8, 18), "weekdays": None},
    {"text": "最近コンビニのプリンにハマってる", "hours": None, "weekdays": None},
    {"text": "爪割れた最悪", "hours": None, "weekdays": None},
    {"text": "洗濯物干してたら雨降ってきた", "hours": (8, 18), "weekdays": None},
    {"text": "今日なんか良いことありそうな予感がする …根拠はないw", "hours": (8, 14), "weekdays": None},
    {"text": "カフェラテ頼んだのにブラック来た 飲むけど", "hours": (8, 16), "weekdays": None},
    {"text": "美容院いつ行こう…前髪限界", "hours": None, "weekdays": None},
    {"text": "最近ずっと同じ曲聴いてる", "hours": None, "weekdays": None},
    {"text": "友達に「最近なにしてるの」って聞かれて 「株…」って言ったら微妙な顔された笑", "hours": None, "weekdays": None},
    {"text": "スマホの充電10%で気づくタイプ", "hours": None, "weekdays": None},
    {"text": "新しいリップ買った", "hours": None, "weekdays": None},
    {"text": "今日1日なにもしてない …いや、生きてただけで偉い", "hours": (18, 23), "weekdays": None},
    {"text": "服選ぶの面倒で毎日似たような格好してるw", "hours": (7, 12), "weekdays": None},
    {"text": "自炊しようと思って材料だけ買って満足するやつ", "hours": None, "weekdays": None},
    {"text": "もう3月終わるの早すぎない？", "hours": None, "weekdays": None},
    {"text": "急に甘いもの食べたくなった", "hours": (14, 22), "weekdays": None},
    {"text": "コンビニの新作スイーツ買ってしまった", "hours": None, "weekdays": None},
    {"text": "あつい", "hours": (10, 18), "weekdays": None},
    {"text": "タピオカ久しぶりに飲んだ", "hours": None, "weekdays": None},
    {"text": "マスクどこいった", "hours": (7, 12), "weekdays": None},
    {"text": "傘忘れた", "hours": (8, 14), "weekdays": None},
    {"text": "推しの新曲やばい", "hours": None, "weekdays": None},
    {"text": "ネイル変えた", "hours": None, "weekdays": None},
    {"text": "宅配便の再配達忘れてた", "hours": None, "weekdays": None},
    {"text": "今日やっと届いた荷物開けるの楽しみ", "hours": None, "weekdays": None},
    {"text": "花粉つらい", "hours": (8, 18), "weekdays": None},
    {"text": "あ、そういえば今日祝日だったw", "hours": (8, 14), "weekdays": None},
]

_WEEKDAY_NAMES = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]


def _filter_stock_lines() -> list[str]:
    """現在時刻・曜日に合うストックラインだけ返す。"""
    now = datetime.now()
    hour = now.hour
    wd = now.weekday()  # 0=Mon..6=Sun
    matched = []
    for entry in _STOCK_LINES:
        # hours filter
        h_range = entry.get("hours")
        if h_range is not None:
            h_start, h_end = h_range
            if not (h_start <= hour <= h_end):
                continue
        # weekday filter
        w_list = entry.get("weekdays")
        if w_list is not None:
            if wd not in w_list:
                continue
        matched.append(entry["text"])
    return matched


class MonologueThread:
    """独り言スレッド: Schedule型 + Idle型を1スレッドで管理"""

    def __init__(self, on_speak):
        """on_speak(text, emotion_tag): GUI表示+TTS再生のコールバック"""
        self.on_speak = on_speak
        self._idle_count_today = 0
        self._idle_date = ""
        self._last_monologue_time = 0.0
        self._fired_schedule_slots: set[str] = set()
        self._schedule_slots: list[dict] = []
        self._load_schedule()

    def _load_schedule(self):
        try:
            p = Path(MONOLOGUE_SCHEDULE_PATH)
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                self._schedule_slots = data.get("slots", [])
                log.info("Monologue schedule loaded: %d slots", len(self._schedule_slots))
        except Exception as e:
            log.warning("Failed to load monologue schedule: %s", e)

    def _in_time_range(self) -> bool:
        try:
            start_s, end_s = MONOLOGUE_TIME_RANGE.split("-")
            now = datetime.now()
            sh, sm = map(int, start_s.split(":"))
            eh, em = map(int, end_s.split(":"))
            start = now.replace(hour=sh, minute=sm, second=0)
            end = now.replace(hour=eh, minute=em, second=0)
            return start <= now <= end
        except Exception:
            return True

    def _reset_daily_counter(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._idle_date != today:
            self._idle_date = today
            self._idle_count_today = 0
            self._fired_schedule_slots.clear()

    def _try_schedule(self):
        """Schedule型: text→固定再生(API不要) / prompt→Claude API生成"""
        if not MONOLOGUE_SCHEDULE_ENABLE or not self._schedule_slots:
            return
        now = datetime.now()
        wd = now.weekday()
        tolerance = MONOLOGUE_SCHEDULE_TOLERANCE_SEC

        for slot in self._schedule_slots:
            slot_time = slot.get("time", "")
            if slot_time in self._fired_schedule_slots:
                continue
            slot_wd = slot.get("weekdays")
            if slot_wd is not None and wd not in slot_wd:
                continue
            try:
                sh, sm = map(int, slot_time.split(":"))
                slot_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
                diff = abs((now - slot_dt).total_seconds())
                if diff <= tolerance:
                    fixed_text = slot.get("text")
                    if fixed_text:
                        emotion = slot.get("emotion_tag", "neutral")
                        log.info("Schedule fixed firing: %s", slot_time)
                        self.on_speak(fixed_text, emotion)
                    else:
                        prompt = slot.get("prompt", "独り言を一言")
                        if prompt == "__FACT_BRIEFING__":
                            log.info("Schedule FACT briefing firing: %s", slot_time)
                            fact_text = _fetch_fact_briefing()
                            if fact_text:
                                self.on_speak(fact_text, "think")
                        else:
                            wd_name = _WEEKDAY_NAMES[wd]
                            prompt = f"今日は{wd_name}。{prompt}"
                            log.info("Schedule API firing: %s", slot_time)
                            result = _call_claude_monologue(prompt)
                            text = result.get("reply_text", "")
                            if text:
                                self.on_speak(text, result.get("emotion_tag", "neutral"))
                    self._last_monologue_time = time.time()
                    self._fired_schedule_slots.add(slot_time)
            except Exception as e:
                log.warning("Schedule monologue error for %s: %s", slot_time, e)

    def _try_idle(self):
        """Idle型: 無操作検知 → 70%ストック / 30%API"""
        if not MONOLOGUE_ENABLE:
            return
        if not self._in_time_range():
            return
        if self._idle_count_today >= MONOLOGUE_MAX_PER_DAY:
            return

        now = time.time()
        idle_sec = now - _last_user_interaction
        cooldown = now - self._last_monologue_time

        if idle_sec < MONOLOGUE_IDLE_SEC:
            return
        if cooldown < MONOLOGUE_COOLDOWN_SEC:
            return

        log.info("Idle monologue firing (idle=%.0fs)", idle_sec)
        filtered = _filter_stock_lines()
        if random.random() < 0.7 and filtered:
            # 70% ストック (API不要, 時刻・曜日フィルタ済み)
            text = random.choice(filtered)
            emotion = "neutral"
        else:
            # 30% API (文脈ある独り言)
            now_dt = datetime.now()
            hour = now_dt.hour
            if hour < 12:
                time_hint = "午前中"
            elif hour < 18:
                time_hint = "午後"
            else:
                time_hint = "夜"
            wd_name = _WEEKDAY_NAMES[now_dt.weekday()]
            prompt = f"今は{wd_name}の{time_hint}。{PERSONA_CALL_USER}がしばらく何も話しかけてこない。独り言を一言つぶやいて。"
            result = _call_claude_monologue(prompt)
            text = result.get("reply_text", "")
            emotion = result.get("emotion_tag", "neutral")

        if text:
            self.on_speak(text, emotion)
            self._idle_count_today += 1
            self._last_monologue_time = time.time()

    def run(self):
        """メインループ (daemonスレッドで呼ぶ)"""
        log.info("MonologueThread started (idle=%s, schedule=%s)",
                 bool(MONOLOGUE_ENABLE), bool(MONOLOGUE_SCHEDULE_ENABLE))
        while True:
            try:
                self._reset_daily_counter()
                self._try_schedule()
                self._try_idle()
            except Exception as e:
                log.error("MonologueThread error: %s", e)
            time.sleep(30)

# ---------------------------------------------------------------------------
# 9.6 KoganeWatcherThread — こがねトレード監視・報告
# ---------------------------------------------------------------------------


class KoganeWatcherThread:
    """こがねトレード監視 → 新規取引検知 → 律がコメント
       + こがねLINE発言検知 → こがねの声でTTS再生"""

    def __init__(self, on_speak):
        self.on_speak = on_speak  # callback(text, emotion, speaker=None)
        self._seen_trades: set[str] = set()
        self._initialized = False
        self._last_msg_ts = 0  # 最後に見たこがねメッセージのタイムスタンプ
        self._msg_initialized = False

    def _fetch_snapshot(self) -> dict | None:
        import requests as req
        try:
            resp = req.get(KOGANE_SNAPSHOT_URL, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.info("Kogane snapshot fetch error: %s", e)
        return None

    def _check_new_trades(self, data: dict):
        trades = data.get("recent_trades", [])
        if not trades:
            return

        # 初回は既存取引を記録するだけ（起動時に全部報告しない）
        if not self._initialized:
            for t in trades:
                key = f"{t.get('ticker', '')}:{t.get('date', '')}"
                self._seen_trades.add(key)
            self._initialized = True
            log.info("Kogane watcher initialized: %d existing trades tracked", len(self._seen_trades))
            return

        new_trades = []
        for t in trades:
            key = f"{t.get('ticker', '')}:{t.get('date', '')}"
            if key not in self._seen_trades:
                self._seen_trades.add(key)
                new_trades.append(t)

        if not new_trades:
            return

        log.info("Kogane: %d new trade(s) detected", len(new_trades))
        lines = []
        for t in new_trades:
            ticker = t.get("ticker", "?")
            name = t.get("name", "")
            ret = t.get("return_pct")
            date_str = t.get("date", "")
            reason = t.get("exit_reason", "")
            ret_str = f"{ret:+.1f}%" if ret is not None else "不明"
            name_str = f" ({name})" if name else ""
            lines.append(f"- {ticker}{name_str}: {ret_str} [{reason}] ({date_str})")

        trade_info = "\n".join(lines)

        perf = data.get("performance", {})
        win_rate = perf.get("win_rate")
        total_ret = perf.get("total_return_pct")
        n_trades = perf.get("n_trades")
        if win_rate is not None:
            trade_info += f"\n\n通算: {n_trades}回取引, 勝率{win_rate}%, 累計リターン{total_ret:+.1f}%"

        result = _call_claude_kogane_report(trade_info)
        text = result.get("reply_text", "")
        if text:
            self.on_speak(text, result.get("emotion_tag", "neutral"))

    def _check_new_messages(self):
        """こがねLINE会話の新着発言を検知 → こがねの声でTTS再生。"""
        if not KOGANE_MESSAGES_URL:
            return
        import requests as req
        try:
            resp = req.get(KOGANE_MESSAGES_URL, timeout=10)
            if resp.status_code != 200:
                log.warning("Kogane messages HTTP %d", resp.status_code)
                return
            data = resp.json()
            messages = data.get("messages", [])
            if not messages:
                return

            # 初回は既存メッセージを記録するだけ
            if not self._msg_initialized:
                self._last_msg_ts = messages[-1].get("ts", 0) if messages else 0
                self._msg_initialized = True
                log.info("Kogane messages initialized (last_ts=%d, count=%d)", self._last_msg_ts, len(messages))
                return

            # 新着のこがね発言だけ抽出
            for msg in messages:
                ts = msg.get("ts", 0)
                if ts > self._last_msg_ts and msg.get("role") == "assistant":
                    text = msg.get("content", "")
                    if text:
                        log.info("Kogane new message (speaker=%d): %s", KOGANE_TTS_SPEAKER_STYLE_ID, text[:50])
                        tts_speak(text, "neutral", speaker=KOGANE_TTS_SPEAKER_STYLE_ID)

            # 最新tsを更新
            latest_ts = messages[-1].get("ts", 0)
            if latest_ts > self._last_msg_ts:
                self._last_msg_ts = latest_ts

        except Exception as e:
            log.info("Kogane messages fetch error: %s", e)

    def run_trades(self):
        """トレード監視スレッド（5分間隔）。"""
        log.info("KoganeTradeWatcher started (url=%s, interval=%ds)",
                 KOGANE_SNAPSHOT_URL, KOGANE_POLL_INTERVAL_SEC)
        while True:
            try:
                data = self._fetch_snapshot()
                if data:
                    self._check_new_trades(data)
            except Exception as e:
                log.error("KoganeTradeWatcher error: %s", e)
            time.sleep(KOGANE_POLL_INTERVAL_SEC)

    def run_messages(self):
        """メッセージ監視スレッド（30秒間隔、独立動作）。"""
        log.info("KoganeMessageWatcher started (url=%s, interval=%ds)",
                 KOGANE_MESSAGES_URL, KOGANE_MESSAGES_POLL_SEC)
        while True:
            try:
                self._check_new_messages()
            except Exception as e:
                log.info("KoganeMessageWatcher error: %s", e)
            time.sleep(KOGANE_MESSAGES_POLL_SEC)

# ---------------------------------------------------------------------------
# 10. tkinter GUI
# ---------------------------------------------------------------------------

_gui_root = None  # for hotkey callbacks
_gui_inp = None
_gui_ptt: PTTRecorder = None

def run_gui():
    """Main GUI loop (must run in main thread)."""
    global _gui_root, _gui_inp, _gui_ptt
    import tkinter as tk

    root = tk.Tk()
    _gui_root = root
    root.title(f"{PERSONA_NAME} Aide V4")
    root.geometry(WINDOW_GEOMETRY)
    root.attributes("-topmost", True)
    root.configure(bg="#1e1e2e")
    root.protocol("WM_DELETE_WINDOW", lambda: _toggle_gui())

    # Title
    tk.Label(root, text=f"{PERSONA_NAME} — V4  |  Enter=送信 / F10=表示切替 / XButton2=PTT",
             bg="#1e1e2e", fg="#6c7086", font=("Segoe UI", 8),
             anchor="w").pack(fill="x", padx=10, pady=(8, 0))

    # Input
    inp = tk.Text(root, height=3, wrap="word",
                  bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
                  font=("Segoe UI", 10), relief="flat", padx=8, pady=6)
    inp.pack(fill="x", padx=10, pady=(6, 0))
    _gui_inp = inp

    # Button frame
    btn_frame = tk.Frame(root, bg="#1e1e2e")
    btn_frame.pack(fill="x", padx=10, pady=4)

    status_label = tk.Label(btn_frame, text="", bg="#1e1e2e", fg="#a6adc8",
                            font=("Segoe UI", 8))
    status_label.pack(side="right")

    # Output log
    out = tk.Text(root, height=10, wrap="word", state="disabled",
                  bg="#181825", fg="#bac2de", font=("Segoe UI", 10),
                  relief="flat", padx=8, pady=6)
    out.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def append_log(msg: str):
        out.config(state="normal")
        out.insert("end", msg + "\n")
        out.see("end")
        out.config(state="disabled")

    def set_status(msg: str):
        status_label.config(text=msg)

    def on_send(event=None):
        text = inp.get("1.0", "end").strip()
        if not text:
            return "break"
        inp.delete("1.0", "end")
        set_status("考え中…")
        threading.Thread(target=_do_send, args=(text,), daemon=True).start()
        return "break"

    def _do_send(text: str):
        global _last_user_interaction
        _last_user_interaction = time.time()
        log_cb = lambda m: root.after(0, append_log, m)
        log_cb(f"[{PERSONA_CALL_USER}] {text}")
        result = _call_claude(text)
        reply = result["reply_text"]
        emotion = result["emotion_tag"]
        elapsed = result.get("elapsed", 0)
        tag = f" [{emotion}]" if emotion != "neutral" else ""
        time_str = f" ({elapsed:.1f}s)" if elapsed else ""
        log_cb(f"[{PERSONA_NAME}]{tag}{time_str} {reply}")
        root.after(0, set_status, f"{emotion} {elapsed:.1f}s")
        tts_speak(reply, emotion)

    inp.bind("<Return>", on_send)
    inp.bind("<Escape>", lambda e: _toggle_gui())

    send_btn = tk.Button(btn_frame, text="送信", command=on_send,
                         bg="#89b4fa", fg="#1e1e2e",
                         font=("Segoe UI", 9, "bold"),
                         relief="flat", padx=16, pady=2)
    send_btn.pack(side="left")

    # PTT — result goes through _do_send
    def ptt_result(text: str):
        root.after(0, lambda: (append_log(f"[PTT] {text}"),))
        _do_send(text)

    def ptt_status(msg: str):
        root.after(0, set_status, msg)

    _gui_ptt = PTTRecorder(on_result=ptt_result, on_status=ptt_status)

    # Welcome
    root.after(100, lambda: append_log(
        f"[{PERSONA_NAME}] V4起動完了。テキスト入力 or XButton2で音声入力。"))

    # Focus
    root.after(300, lambda: inp.focus_force())

    # Start hotkey thread
    start_hotkey_thread(
        on_toggle_gui=lambda: root.after(0, _toggle_gui),
        on_ptt_start=lambda: root.after(0, _gui_ptt.start),
        on_ptt_stop=lambda: root.after(0, _gui_ptt.stop),
    )

    # Monologue / Kogane speak callback
    def monologue_speak(text: str, emotion: str):
        tag = f" [{emotion}]" if emotion != "neutral" else ""
        root.after(0, append_log, f"[{PERSONA_NAME}]{tag} {text}")
        tts_speak(text, emotion)

    # Start Monologue thread
    if MONOLOGUE_ENABLE or MONOLOGUE_SCHEDULE_ENABLE:
        mono = MonologueThread(on_speak=monologue_speak)
        threading.Thread(target=mono.run, daemon=True, name="Monologue").start()

    # Start Kogane watcher thread
    if KOGANE_ENABLE:
        kogane = KoganeWatcherThread(on_speak=monologue_speak)
        threading.Thread(target=kogane.run_trades, daemon=True, name="KoganeTradeWatcher").start()
        if KOGANE_MESSAGES_URL:
            threading.Thread(target=kogane.run_messages, daemon=True, name="KoganeMessageWatcher").start()

    # Start shared knowledge sync
    if SHARED_KNOWLEDGE_URL and SHARED_KNOWLEDGE_TOKEN:
        _shared_knowledge_pull()  # 初回即pull
        threading.Thread(target=_shared_knowledge_sync_loop, daemon=True, name="SharedKnowledgeSync").start()

    root.mainloop()

_gui_visible = True

def _toggle_gui():
    """Toggle GUI window visibility."""
    global _gui_visible
    if _gui_root is None:
        return
    if _gui_visible:
        _gui_root.withdraw()
        _gui_visible = False
    else:
        _gui_root.deiconify()
        _gui_root.lift()
        _gui_root.focus_force()
        if _gui_inp:
            _gui_inp.focus_force()
        _gui_visible = True

# ---------------------------------------------------------------------------
# 11. Main
# ---------------------------------------------------------------------------

def main():
    log.info("律 Aide V4 starting (Phase 1-4)")

    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY is not set. Claude API calls will fail.")

    # List audio input devices for diagnostic
    try:
        import sounddevice as sd
        log.info("=== Audio Input Devices ===")
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                marker = " <<<DEFAULT" if i == sd.default.device[0] else ""
                log.info("  #%d: %s (in=%d ch, rate=%.0f)%s",
                         i, d['name'], d['max_input_channels'],
                         d['default_samplerate'], marker)
        log.info("Set RITSU_STT_INPUT_DEVICE=<number> in .env to change input device")
    except Exception as e:
        log.warning("Could not list audio devices: %s", e)

    # Start TTS worker
    tts_thread = threading.Thread(target=_tts_worker, daemon=True, name="TTS")
    tts_thread.start()

    # Preload STT model in background
    threading.Thread(target=_get_stt_model, daemon=True, name="STT-init").start()

    # Run GUI (blocks in main thread; hotkeys started inside run_gui)
    try:
        run_gui()
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        _tts_queue.put(None)

    log.info("律 Aide V4 stopped.")

if __name__ == "__main__":
    main()
