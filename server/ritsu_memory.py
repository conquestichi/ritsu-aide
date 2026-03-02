"""
ritsu_memory.py — 律の長期記憶モジュール

機能:
  1. 会話要約（summaries）: N往復ごとに自動要約を生成・保存
  2. 知識抽出（knowledge）: 会話からユーザーの好み・事実・決定事項を自動抽出
  3. 明示記憶（"覚えて"コマンド）: ユーザーが直接指示した内容を保存
  4. プロンプト注入: system prompt に要約+知識を自動注入

依存: assistant.sqlite（既存DB）に2テーブル追加
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STATE_DIR = Path(os.getenv("RITSU_STATE_DIR", "/srv/ritsu/state"))
SQLITE_PATH = STATE_DIR / "assistant.sqlite"

# 何往復ごとに要約を生成するか（user+assistant で 1往復）
SUMMARIZE_EVERY_N = int(os.getenv("RITSU_SUMMARIZE_EVERY", "8"))

# knowledge に保持する最大件数（古い順に自動アーカイブ）
MAX_KNOWLEDGE = int(os.getenv("RITSU_MAX_KNOWLEDGE", "200"))

# プロンプトに注入する要約の最大件数
MAX_SUMMARY_INJECT = int(os.getenv("RITSU_MAX_SUMMARY_INJECT", "5"))

# プロンプトに注入する知識の最大件数
MAX_KNOWLEDGE_INJECT = int(os.getenv("RITSU_MAX_KNOWLEDGE_INJECT", "30"))


# ---------------------------------------------------------------------------
# DB Schema
# ---------------------------------------------------------------------------
MEMORY_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    turn_start INTEGER NOT NULL,      -- 要約対象の最初の turn.id
    turn_end INTEGER NOT NULL,        -- 要約対象の最後の turn.id
    turn_count INTEGER NOT NULL,      -- 要約対象のターン数
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summaries_conv
    ON summaries(conversation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT 'fact',  -- fact / preference / decision / memo
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',    -- auto / user / system
    conversation_id TEXT,
    confidence REAL NOT NULL DEFAULT 0.8,   -- 0.0-1.0
    is_active INTEGER NOT NULL DEFAULT 1,   -- 0=archived
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_active
    ON knowledge(is_active, category, updated_at DESC);

CREATE TABLE IF NOT EXISTS memory_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(SQLITE_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.executescript(MEMORY_TABLES_SQL)
    return con


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def get_unsummarized_turns(conversation_id: str) -> Tuple[List[Dict[str, Any]], int]:
    """
    最後の要約以降のターンを取得。
    Returns: (turns_list, last_summary_turn_end)
    """
    con = _connect()
    try:
        # 最新の要約の turn_end を取得
        row = con.execute(
            "SELECT turn_end FROM summaries WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        last_end = int(row["turn_end"]) if row else 0

        # それ以降のターンを取得
        rows = con.execute(
            "SELECT id, role, content, ts FROM turns WHERE conversation_id=? AND id>? ORDER BY ts, id",
            (conversation_id, last_end),
        ).fetchall()

        turns = [{"id": r["id"], "role": r["role"], "content": r["content"], "ts": r["ts"]} for r in rows]
        return turns, last_end
    finally:
        con.close()


def should_summarize(conversation_id: str) -> bool:
    """要約が必要か判定（未要約ターンが閾値を超えたか）"""
    turns, _ = get_unsummarized_turns(conversation_id)
    # user+assistant のペア数で判定
    user_count = sum(1 for t in turns if t["role"] == "user")
    return user_count >= SUMMARIZE_EVERY_N


def save_summary(
    conversation_id: str,
    summary: str,
    turn_start: int,
    turn_end: int,
    turn_count: int,
) -> int:
    """要約を保存"""
    con = _connect()
    try:
        cur = con.execute(
            "INSERT INTO summaries (conversation_id, summary, turn_start, turn_end, turn_count, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (conversation_id, summary, turn_start, turn_end, turn_count, int(time.time())),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def get_recent_summaries(conversation_id: str, limit: int = MAX_SUMMARY_INJECT) -> List[Dict[str, Any]]:
    """直近の要約を取得"""
    con = _connect()
    try:
        rows = con.execute(
            "SELECT id, summary, turn_count, created_at FROM summaries "
            "WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        result = [dict(r) for r in rows]
        result.reverse()  # 時系列順に
        return result
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------
def add_knowledge(
    content: str,
    category: str = "fact",
    source: str = "auto",
    conversation_id: Optional[str] = None,
    confidence: float = 0.8,
) -> int:
    """知識を追加（重複チェック付き）"""
    con = _connect()
    try:
        # 類似内容の重複チェック（完全一致のみ。意味的重複は将来対応）
        existing = con.execute(
            "SELECT id FROM knowledge WHERE content=? AND is_active=1",
            (content,),
        ).fetchone()
        if existing:
            # 既存を更新（confidence/updated_at）
            con.execute(
                "UPDATE knowledge SET confidence=MAX(confidence,?), updated_at=? WHERE id=?",
                (confidence, int(time.time()), existing["id"]),
            )
            con.commit()
            return existing["id"]

        cur = con.execute(
            "INSERT INTO knowledge (category, content, source, conversation_id, confidence, is_active, created_at, updated_at) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (category, content, source, conversation_id, confidence, int(time.time()), int(time.time())),
        )
        con.commit()

        # MAX_KNOWLEDGE を超えたら古い順にアーカイブ
        _prune_knowledge(con)

        return cur.lastrowid
    finally:
        con.close()


def _prune_knowledge(con: sqlite3.Connection) -> None:
    """知識が上限を超えたら古い順にアーカイブ"""
    count = con.execute("SELECT COUNT(*) as n FROM knowledge WHERE is_active=1").fetchone()["n"]
    if count <= MAX_KNOWLEDGE:
        return
    excess = count - MAX_KNOWLEDGE
    con.execute(
        "UPDATE knowledge SET is_active=0 WHERE id IN ("
        "  SELECT id FROM knowledge WHERE is_active=1 ORDER BY updated_at ASC LIMIT ?"
        ")",
        (excess,),
    )
    con.commit()


def get_active_knowledge(limit: int = MAX_KNOWLEDGE_INJECT) -> List[Dict[str, Any]]:
    """アクティブな知識を取得（カテゴリ順→更新日順）"""
    con = _connect()
    try:
        rows = con.execute(
            "SELECT id, category, content, source, confidence, created_at, updated_at "
            "FROM knowledge WHERE is_active=1 "
            "ORDER BY "
            "  CASE category "
            "    WHEN 'preference' THEN 1 "
            "    WHEN 'fact' THEN 2 "
            "    WHEN 'decision' THEN 3 "
            "    WHEN 'memo' THEN 4 "
            "    ELSE 5 END, "
            "  updated_at DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def delete_knowledge(knowledge_id: int) -> bool:
    """知識をアーカイブ（論理削除）"""
    con = _connect()
    try:
        cur = con.execute(
            "UPDATE knowledge SET is_active=0, updated_at=? WHERE id=? AND is_active=1",
            (int(time.time()), knowledge_id),
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def search_knowledge(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """知識を検索（SQLite LIKE）"""
    con = _connect()
    try:
        rows = con.execute(
            "SELECT id, category, content, source, confidence FROM knowledge "
            "WHERE is_active=1 AND content LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# LLM-based extraction (要約 + 知識抽出)
# ---------------------------------------------------------------------------
SUMMARIZE_PROMPT = """\
以下の会話履歴を簡潔に要約してください。
重要な決定事項、依頼内容、結論、未完了タスクを漏らさず含めてください。
200文字以内の日本語で。

会話履歴:
{turns_text}

要約:"""

EXTRACT_KNOWLEDGE_PROMPT = """\
以下の会話から、ユーザーについての重要な事実・好み・決定事項を抽出してください。
既知の情報（下記）と重複するものは除外してください。

既知:
{existing_knowledge}

会話:
{turns_text}

JSON配列で返してください。新しい情報がなければ空配列 [] を返してください。
形式: [{"category": "fact|preference|decision|memo", "content": "...", "confidence": 0.0-1.0}]
JSON配列のみ出力。前置き・装飾・コードブロック禁止。"""


def _format_turns(turns: List[Dict[str, Any]]) -> str:
    """ターンをテキスト形式に変換"""
    lines = []
    for t in turns:
        role = "ユーザー" if t["role"] == "user" else "律"
        lines.append(f"{role}: {t['content']}")
    return "\n".join(lines)


def generate_summary(turns: List[Dict[str, Any]], client) -> Optional[str]:
    """
    OpenAI APIで会話要約を生成。
    client: OpenAI() インスタンス
    """
    if not turns:
        return None

    turns_text = _format_turns(turns)
    prompt = SUMMARIZE_PROMPT.format(turns_text=turns_text)

    try:
        resp = client.chat.completions.create(
            model=os.getenv("RITSU_SUMMARY_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3,
        )
        summary = resp.choices[0].message.content.strip()
        return summary if summary else None
    except Exception as e:
        print(f"[MEMORY] summarize failed: {e}")
        return None


def extract_knowledge(
    turns: List[Dict[str, Any]],
    client,
    conversation_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    OpenAI APIで知識を抽出。
    """
    if not turns:
        return []

    # 既存知識を取得（重複排除用）
    existing = get_active_knowledge(limit=50)
    existing_text = "\n".join([f"- [{k['category']}] {k['content']}" for k in existing]) or "(なし)"

    turns_text = _format_turns(turns)
    prompt = EXTRACT_KNOWLEDGE_PROMPT.format(
        existing_knowledge=existing_text,
        turns_text=turns_text,
    )

    try:
        resp = client.chat.completions.create(
            model=os.getenv("RITSU_SUMMARY_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
        # JSON配列をパース（```json ... ``` 対応）
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        items = json.loads(raw)
        if not isinstance(items, list):
            return []

        saved = []
        for item in items:
            if not isinstance(item, dict) or not item.get("content"):
                continue
            kid = add_knowledge(
                content=str(item["content"]),
                category=str(item.get("category", "fact")),
                source="auto",
                conversation_id=conversation_id,
                confidence=float(item.get("confidence", 0.7)),
            )
            saved.append({"id": kid, **item})
        return saved
    except Exception as e:
        print(f"[MEMORY] extract_knowledge failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Explicit memory commands (ユーザーの "覚えて" 検出)
# ---------------------------------------------------------------------------
_REMEMBER_PATTERNS = [
    r"覚えて[。、：:]*\s*(.+)",
    r"記憶して[。、：:]*\s*(.+)",
    r"メモして[。、：:]*\s*(.+)",
    r"忘れないで[。、：:]*\s*(.+)",
    r"remember[:\s]+(.+)",
]

_FORGET_PATTERNS = [
    r"忘れて[。、：:]*\s*(.+)",
    r"削除して[。、：:]*\s*(.+)",
    r"消して[。、：:]*\s*(.+)",
    r"forget[:\s]+(.+)",
]


def detect_memory_command(user_text: str) -> Optional[Dict[str, str]]:
    """
    ユーザーの入力から記憶コマンドを検出。
    Returns: {"action": "remember"|"forget"|"list", "content": "..."} or None
    """
    text = user_text.strip()

    # 記憶一覧
    if re.search(r"(何を覚えて|記憶一覧|知識一覧|what do you remember|what do you know)", text, re.I):
        return {"action": "list", "content": ""}

    for pat in _REMEMBER_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            return {"action": "remember", "content": m.group(1).strip()}

    for pat in _FORGET_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            return {"action": "forget", "content": m.group(1).strip()}

    return None


def handle_memory_command(cmd: Dict[str, str]) -> Optional[str]:
    """
    記憶コマンドを実行して応答テキストを返す。
    Returns: 応答テキスト（Noneなら通常会話を継続）
    """
    action = cmd["action"]
    content = cmd["content"]

    if action == "remember":
        kid = add_knowledge(content=content, category="memo", source="user", confidence=1.0)
        return f"了解、覚えた。（記憶ID: {kid}）"

    if action == "forget":
        results = search_knowledge(content, limit=5)
        if not results:
            return f"「{content}」に該当する記憶が見つからない。"
        deleted = 0
        for r in results:
            if delete_knowledge(r["id"]):
                deleted += 1
        return f"「{content}」に関する記憶を{deleted}件削除した。"

    if action == "list":
        items = get_active_knowledge(limit=20)
        if not items:
            return "今のところ特に覚えていることはない。"
        lines = []
        for k in items:
            lines.append(f"[{k['category']}] {k['content']}")
        return "覚えていること:\n" + "\n".join(lines)

    return None


# ---------------------------------------------------------------------------
# Auto-process: 会話後に呼ぶ（要約 + 知識抽出）
# ---------------------------------------------------------------------------
def auto_process_memory(conversation_id: str, client) -> Dict[str, Any]:
    """
    会話の後に呼ぶ。必要なら要約生成 + 知識抽出を実行。
    非同期で呼ぶのが望ましいが、同期でも動く（LLM呼び出し分の遅延あり）。

    Returns: {"summarized": bool, "knowledge_added": int}
    """
    result = {"summarized": False, "knowledge_added": 0}

    turns, last_end = get_unsummarized_turns(conversation_id)
    if not turns:
        return result

    user_count = sum(1 for t in turns if t["role"] == "user")

    # 要約判定
    if user_count >= SUMMARIZE_EVERY_N:
        summary = generate_summary(turns, client)
        if summary:
            save_summary(
                conversation_id=conversation_id,
                summary=summary,
                turn_start=turns[0]["id"],
                turn_end=turns[-1]["id"],
                turn_count=len(turns),
            )
            result["summarized"] = True
            print(f"[MEMORY] summary saved for {conversation_id}: {summary[:80]}...")

        # 知識抽出（要約と同タイミングで実行）
        extracted = extract_knowledge(turns, client, conversation_id)
        result["knowledge_added"] = len(extracted)
        if extracted:
            print(f"[MEMORY] {len(extracted)} knowledge items extracted")

    return result


# ---------------------------------------------------------------------------
# Prompt injection: system prompt に記憶を注入
# ---------------------------------------------------------------------------
def build_memory_context(conversation_id: str) -> str:
    """
    system prompt に追加する記憶コンテキストを生成。
    build_instructions() の結果にこれを追加する。
    """
    sections = []

    # 1. 過去の要約
    summaries = get_recent_summaries(conversation_id)
    if summaries:
        lines = []
        for s in summaries:
            lines.append(f"- {s['summary']}")
        sections.append(
            "【過去の会話の要約（古い順）】\n" + "\n".join(lines)
        )

    # 2. 知識
    knowledge = get_active_knowledge()
    if knowledge:
        by_cat: Dict[str, List[str]] = {}
        for k in knowledge:
            cat = k["category"]
            by_cat.setdefault(cat, []).append(k["content"])

        lines = []
        cat_labels = {
            "preference": "好み・設定",
            "fact": "事実",
            "decision": "決定事項",
            "memo": "メモ",
        }
        for cat, items in by_cat.items():
            label = cat_labels.get(cat, cat)
            for item in items:
                lines.append(f"- [{label}] {item}")

        sections.append(
            "【ユーザーについて知っていること】\n" + "\n".join(lines)
        )

    if not sections:
        return ""

    return (
        "\n\n--- 長期記憶 ---\n"
        "以下はこれまでの会話から学んだ情報。必要に応じて自然に活用すること。"
        "ただし「記憶から～」と明言する必要はない。\n\n"
        + "\n\n".join(sections)
        + "\n--- 長期記憶ここまで ---\n"
    )


# ---------------------------------------------------------------------------
# API endpoints (FastAPI router)
# ---------------------------------------------------------------------------
from fastapi import APIRouter, Query as FQuery
from pydantic import BaseModel, Field as PField

memory_router = APIRouter(prefix="/memory", tags=["memory"])


class KnowledgeIn(BaseModel):
    content: str
    category: str = "memo"


class KnowledgeOut(BaseModel):
    id: int
    category: str
    content: str
    source: str
    confidence: float


@memory_router.get("/knowledge")
def api_list_knowledge(q: str = FQuery("", description="検索クエリ"), limit: int = 30):
    if q:
        items = search_knowledge(q, limit=limit)
    else:
        items = get_active_knowledge(limit=limit)
    return {"items": items, "count": len(items)}


@memory_router.post("/knowledge")
def api_add_knowledge(body: KnowledgeIn):
    kid = add_knowledge(content=body.content, category=body.category, source="user", confidence=1.0)
    return {"ok": True, "id": kid}


@memory_router.delete("/knowledge/{kid}")
def api_delete_knowledge(kid: int):
    ok = delete_knowledge(kid)
    return {"ok": ok}


@memory_router.get("/summaries")
def api_list_summaries(conversation_id: str = "vm", limit: int = 10):
    items = get_recent_summaries(conversation_id, limit=limit)
    return {"items": items, "count": len(items)}


@memory_router.get("/status")
def api_memory_status():
    con = _connect()
    try:
        k_count = con.execute("SELECT COUNT(*) as n FROM knowledge WHERE is_active=1").fetchone()["n"]
        s_count = con.execute("SELECT COUNT(*) as n FROM summaries").fetchone()["n"]
        k_archived = con.execute("SELECT COUNT(*) as n FROM knowledge WHERE is_active=0").fetchone()["n"]
    finally:
        con.close()
    return {
        "knowledge_active": k_count,
        "knowledge_archived": k_archived,
        "summaries_total": s_count,
        "config": {
            "summarize_every_n": SUMMARIZE_EVERY_N,
            "max_knowledge": MAX_KNOWLEDGE,
        },
    }
