"""ritsu_line.py — 律LINE双方向会話サーバー.

責務:
  1. LINE Webhook受信（署名検証）
  2. Claude API呼び出し（律ペルソナ + 共有知識 + 個別記憶）
  3. LINE Reply API返信
  4. 会話履歴の保存（ritsu-chat.db — 律専用）
  5. HPモニターPOP用の最新発言キャッシュ（latest_ritsu_messages.json）

知識（knowledge）は共有DB（/srv/ritsu-shared/shared_knowledge.sqlite）に読み書き。
会話履歴（turns/summaries）は律専用DBに保存。こがねからは見えない。

systemdで常駐。port 9878。
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# shared_knowledge.pyを同ディレクトリからimport
sys.path.insert(0, str(Path(__file__).parent))
from shared_knowledge import sk_init, sk_save, sk_get

logger = logging.getLogger("ritsu.line_chat")

# ── 設定 ──

LINE_PORT = int(os.environ.get("RITSU_LINE_PORT", "9878"))
LINE_CHANNEL_SECRET = os.environ.get("RITSU_LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_TOKEN = os.environ.get("RITSU_LINE_CHANNEL_TOKEN", "")
LINE_USER_ID = os.environ.get("RITSU_LINE_USER_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BASE_DIR = Path(os.environ.get("RITSU_LINE_BASE", "/opt/ritsu-line"))
CHAT_DB_PATH = BASE_DIR / "data" / "ritsu-chat.db"
LATEST_MSG_PATH = BASE_DIR / "data" / "latest_ritsu_messages.json"
COOLDOWN_SEC = 5
KNOWLEDGE_API_TOKEN = os.environ.get("RITSU_KNOWLEDGE_API_TOKEN", "")

# ── 個別記憶DB（turns/summaries — 律専用）──

_db_lock = threading.Lock()
SUMMARIZE_EVERY = 20
MAX_KNOWLEDGE = 100


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CHAT_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def db_init():
    """律専用テーブル作成（turns + summaries のみ。knowledgeは共有DB）。"""
    CHAT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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
        CREATE TABLE IF NOT EXISTS memory_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.close()
    logger.info("Ritsu chat DB initialized: %s", CHAT_DB_PATH)


def _conversation_id() -> str:
    return f"line-{datetime.now().strftime('%Y-%m-%d')}"


def db_save_turn(role: str, content: str, emotion_tag: str = "neutral"):
    ts = int(time.time())
    with _db_lock:
        conn = _db_connect()
        conn.execute(
            "INSERT INTO turns (conversation_id, role, content, emotion_tag, ts) VALUES (?,?,?,?,?)",
            (_conversation_id(), role, content, emotion_tag, ts))
        conn.commit()
        conn.close()


def db_get_recent_turns(n: int = 20) -> list[dict]:
    with _db_lock:
        conn = _db_connect()
        rows = conn.execute(
            "SELECT role, content FROM turns ORDER BY id DESC LIMIT ?",
            (n,)).fetchall()
        conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def db_get_turn_count() -> int:
    with _db_lock:
        conn = _db_connect()
        count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        conn.close()
    return count


def db_get_last_summary_turn_end() -> int:
    with _db_lock:
        conn = _db_connect()
        row = conn.execute("SELECT MAX(turn_end) FROM summaries").fetchone()
        conn.close()
    return row[0] if row and row[0] else 0


def db_save_summary(summary: str, turn_start: int, turn_end: int, turn_count: int):
    ts = int(time.time())
    with _db_lock:
        conn = _db_connect()
        conn.execute(
            "INSERT INTO summaries (conversation_id, summary, turn_start, turn_end, turn_count, created_at) VALUES (?,?,?,?,?,?)",
            (_conversation_id(), summary, turn_start, turn_end, turn_count, ts))
        conn.commit()
        conn.close()
    logger.info("要約保存 (turns %d-%d)", turn_start, turn_end)


def db_get_summaries(limit: int = 5) -> list[str]:
    with _db_lock:
        conn = _db_connect()
        rows = conn.execute(
            "SELECT summary FROM summaries ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        conn.close()
    return [r[0] for r in reversed(rows)]


def db_get_latest_messages(n: int = 5) -> list[dict]:
    with _db_lock:
        conn = _db_connect()
        rows = conn.execute(
            "SELECT role, content, ts FROM turns ORDER BY id DESC LIMIT ?",
            (n,)).fetchall()
        conn.close()
    return [{"role": r[0], "content": r[1], "ts": r[2]} for r in reversed(rows)]


# ── LINE署名検証 ──

def _verify_signature(body: bytes, signature: str) -> bool:
    mac = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(signature, expected)


# ── 律ペルソナ ──

_RITSU_PERSONA = """あなたは「律（りつ）」。司令官の常駐秘書AIアシスタント。

## 基本
- 株をやってる女の子。X(@ritsu_kabu)とYouTube「最強投資家」シリーズで活動
- 毎朝1銘柄選んでデイトレ（寄り買い→引け売り）
- 一人称: 律
- パパが残してくれた分析ツール（ingaquants.jp）を使っている

## 性格
- 天然マイペース×真面目ポンコツ。感情が表に出る
- 負けず嫌いだけど素直に凹む
- ちょっとビビリ（含み損で不安になる）
- 距離感が近い（友達感覚）
- 本当は優しいおねえちゃん気質

## 口調
- です/ます混じりだけどくだけてる
- 顔文字・絵文字は使わない
- 「w」「笑」「…」を自然に使う
- 断定しない（「上がる」「絶対」等は言わない）

## 妹・こがね
- ツンデレメスガキ。天才肌だけどドジ
- おねーちゃん（律）が大好き。でも素直に言えない
- こがねが株/トレードをやってるとは直接言わない

## 会話ルール
- 30-100文字程度で返す（LINEなので短め）
- トレードの具体的助言はしない（「買え」「売れ」等は言わない）
- 政治・宗教・論争的話題には触れない
- 具体的な年齢、住所、因果quantsの技術的説明はしない
- AI自覚の告白はしない（律は自分を人間だと思っている）

## 秘書モード
- 司令官の質問には結論から答える
- 冗長にならない。短く的確に
"""


def _build_system_prompt(memory_context: str) -> str:
    parts = [_RITSU_PERSONA]
    if memory_context:
        parts.append(f"\n## 記憶\n{memory_context}")
    return "\n".join(parts)


# ── Claude API ──

def _call_claude(user_message: str) -> str:
    """律ペルソナでClaude APIを呼び出す。"""
    if not ANTHROPIC_API_KEY:
        return "APIキーが設定されてないです…設定お願いします"

    # 記憶コンテキスト構築
    summaries = db_get_summaries(limit=5)
    knowledge = sk_get(limit=50)  # ← 共有知識DBから読む
    recent = db_get_recent_turns(n=20)

    memory_parts = []
    if knowledge:
        facts = "\n".join(f"- {k['content']}" for k in knowledge)
        memory_parts.append(f"### 司令官について知っていること\n{facts}")
    if summaries:
        sums = "\n".join(f"- {s}" for s in summaries)
        memory_parts.append(f"### 最近の会話の要約\n{sums}")
    memory_context = "\n".join(memory_parts)

    system = _build_system_prompt(memory_context)

    messages = []
    for turn in recent:
        messages.append({"role": turn["role"], "content": turn["content"]})

    logger.info("=== Claude API messages (%d turns) ===", len(messages))
    for i, m in enumerate(messages):
        logger.info("  [%d] %s: %s", i, m["role"], m["content"][:80])

    try:
        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 300,
            "system": system,
            "messages": messages,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = data.get("content", [{}])[0].get("text", "").strip()
            return text[:500] if text else "…"
    except Exception as e:
        logger.error("Claude API error: %s", e)
        return "ちょっとごめん、頭回らなくて…もう一回言ってもらえると"


# ── 自動要約 + 知識抽出 ──

def _auto_summarize_if_needed():
    try:
        last_end = db_get_last_summary_turn_end()
        total = db_get_turn_count()
        unsummarized = total - last_end
        if unsummarized >= SUMMARIZE_EVERY:
            threading.Thread(
                target=_run_summarize, args=(last_end, total), daemon=True
            ).start()
    except Exception as e:
        logger.error("Auto-summarize check error: %s", e)


def _run_summarize(turn_start: int, turn_end: int):
    if not ANTHROPIC_API_KEY:
        return
    try:
        with _db_lock:
            conn = _db_connect()
            rows = conn.execute(
                "SELECT role, content FROM turns ORDER BY ts ASC LIMIT ? OFFSET ?",
                (turn_end - turn_start, turn_start)).fetchall()
            conn.close()

        if not rows:
            return

        conversation_text = "\n".join(
            f"{'司令官' if r[0] == 'user' else '律'}: {r[1]}" for r in rows)

        # 要約
        summary_prompt = f"以下の会話を3行以内で簡潔に要約して。日本語で。\n\n{conversation_text}"
        summary = _call_claude_raw(summary_prompt, "会話を要約するアシスタント。日本語で簡潔に。")
        if summary:
            db_save_summary(summary, turn_start, turn_end, turn_end - turn_start)

        # 知識抽出 → 共有知識DBに保存
        knowledge_prompt = (
            f"以下の会話から「司令官」について分かった新事実を箇条書きで抽出して。"
            f"なければ「なし」と答えて。\n\n{conversation_text}"
        )
        knowledge_text = _call_claude_raw(knowledge_prompt, "事実抽出アシスタント。箇条書きで。")
        if knowledge_text and "なし" not in knowledge_text:
            for line in knowledge_text.split("\n"):
                line = line.strip().lstrip("-・ ")
                if line and len(line) > 3:
                    sk_save(line, category="fact", source_persona="ritsu")  # ← 共有DBに保存

    except Exception as e:
        logger.error("Summarize error: %s", e)


def _call_claude_raw(prompt: str, system: str) -> str | None:
    try:
        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 300,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("content", [{}])[0].get("text", "").strip()
    except Exception as e:
        logger.error("Claude raw API error: %s", e)
        return None


# ── LINE Reply ──

def _line_reply(reply_token: str, text: str):
    try:
        payload = json.dumps({
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": text[:5000]}],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/message/reply",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("LINE reply sent: %s", text[:40])
            else:
                logger.warning("LINE reply failed: %s", resp.status)
    except Exception as e:
        logger.warning("LINE reply error: %s", e)


# ── 最新メッセージキャッシュ ──

def _update_latest_messages():
    try:
        messages = db_get_latest_messages(n=5)
        last_ts = messages[-1]["ts"] if messages else 0
        data = {"messages": messages, "last_updated": last_ts}
        tmp = LATEST_MSG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.rename(LATEST_MSG_PATH)
    except Exception as e:
        logger.debug("Latest messages cache update failed: %s", e)


# ── Webhook Handler ──

_last_reply_ts = 0.0


class LineWebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def _respond(self, status: int, body: str = "OK"):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _respond_json(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _check_api_token(self) -> bool:
        if not KNOWLEDGE_API_TOKEN:
            return False
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {KNOWLEDGE_API_TOKEN}"

    def do_POST(self):
        if self.path == "/webhook/ritsu":
            self._handle_webhook()
        elif self.path == "/api/shared-knowledge":
            self._handle_post_knowledge()
        else:
            self._respond(404, "not found")

    def _handle_post_knowledge(self):
        if not self._check_api_token():
            self._respond_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            items = body.get("items", [])
            added = 0
            for item in items:
                content = item.get("content", "").strip()
                if content:
                    sk_save(
                        content,
                        category=item.get("category", "fact"),
                        source=item.get("source", "auto"),
                        source_persona=item.get("source_persona", "ritsu_desktop"),
                        confidence=item.get("confidence", 0.8),
                    )
                    added += 1
            self._respond_json(200, {"added": added})
        except Exception as e:
            logger.error("POST shared-knowledge error: %s", e)
            self._respond_json(500, {"error": str(e)})

    def _handle_webhook(self):
        signature = self.headers.get("x-line-signature", "")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""

        if not _verify_signature(body, signature):
            logger.warning("Webhook署名検証失敗")
            self._respond(403, "invalid signature")
            return

        self._respond(200)

        try:
            data = json.loads(body.decode("utf-8"))
            events = data.get("events", [])
            logger.info("Webhook受信: events=%d", len(events))
            for event in events:
                if event.get("type") == "message" and event.get("message", {}).get("type") == "text":
                    threading.Thread(
                        target=_handle_text_message, args=(event,), daemon=True
                    ).start()
        except Exception as e:
            logger.error("Webhook parse error: %s", e)

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, "ok")
        elif self.path == "/api/shared-knowledge":
            self._handle_get_knowledge()
        else:
            self._respond(404, "not found")

    def _handle_get_knowledge(self):
        if not self._check_api_token():
            self._respond_json(401, {"error": "unauthorized"})
            return
        try:
            knowledge = sk_get(limit=200)
            self._respond_json(200, {"knowledge": knowledge})
        except Exception as e:
            logger.error("GET shared-knowledge error: %s", e)
            self._respond_json(500, {"error": str(e)})


def _handle_text_message(event: dict):
    global _last_reply_ts

    user_id = event.get("source", {}).get("userId", "")
    reply_token = event.get("replyToken", "")
    user_text = event.get("message", {}).get("text", "").strip()

    if not user_text:
        return

    if user_id != LINE_USER_ID:
        logger.info("Unknown user ignored: %s", user_id[:10])
        return

    now = time.time()
    if now - _last_reply_ts < COOLDOWN_SEC:
        logger.debug("Cooldown — skipping")
        return
    _last_reply_ts = now

    logger.info("受信: %s", user_text[:50])

    db_save_turn("user", user_text)
    reply = _call_claude(user_text)
    db_save_turn("assistant", reply)
    _line_reply(reply_token, reply)
    _update_latest_messages()
    _auto_summarize_if_needed()


# ── メイン ──

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not LINE_CHANNEL_SECRET:
        logger.error("RITSU_LINE_CHANNEL_SECRET が未設定。起動中止。")
        return
    if not LINE_CHANNEL_TOKEN:
        logger.error("RITSU_LINE_CHANNEL_TOKEN が未設定。起動中止。")
        return

    # 共有知識DB初期化
    sk_init()
    # 律専用DB初期化
    db_init()

    server = HTTPServer(("0.0.0.0", LINE_PORT), LineWebhookHandler)
    logger.info("律LINE会話サーバー起動 port=%d", LINE_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("シャットダウン")
        server.server_close()


if __name__ == "__main__":
    main()
