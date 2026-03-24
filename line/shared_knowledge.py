"""shared_knowledge.py — 律/こがね共有知識DB.

/srv/ritsu-shared/shared_knowledge.sqlite に知識を読み書きする。
turns/summariesは各キャラの個別DBに持つ。知識だけ共有。

使い方:
  from shared_knowledge import sk_init, sk_save, sk_get, sk_deactivate
"""
import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger("shared_knowledge")

SHARED_DB_PATH = Path("/srv/ritsu-shared/shared_knowledge.sqlite")
MAX_KNOWLEDGE = 200

_sk_lock = threading.Lock()


def _sk_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SHARED_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def sk_init():
    """共有知識テーブルを初期化。"""
    SHARED_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _sk_connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'fact',
            content TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'auto',
            source_persona TEXT NOT NULL DEFAULT 'unknown',
            confidence REAL NOT NULL DEFAULT 0.8,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
    """)
    conn.close()
    logger.info("Shared knowledge DB initialized: %s", SHARED_DB_PATH)


def sk_save(content: str, category: str = "fact", source: str = "auto",
            source_persona: str = "unknown", confidence: float = 0.8):
    """共有知識に保存。重複は無視、上限超過時は最古のauto知識を削除。"""
    ts = int(time.time())
    with _sk_lock:
        conn = _sk_connect()
        existing = conn.execute(
            "SELECT id FROM knowledge WHERE content=? AND is_active=1",
            (content,)).fetchone()
        if existing:
            conn.close()
            return
        count = conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE is_active=1").fetchone()[0]
        if count >= MAX_KNOWLEDGE:
            conn.execute(
                "DELETE FROM knowledge WHERE id = "
                "(SELECT id FROM knowledge WHERE is_active=1 AND source != 'explicit' "
                "ORDER BY updated_at ASC LIMIT 1)")
        conn.execute(
            "INSERT INTO knowledge "
            "(category, content, source, source_persona, confidence, is_active, created_at, updated_at) "
            "VALUES (?,?,?,?,?,1,?,?)",
            (category, content, source, source_persona, confidence, ts, ts))
        conn.commit()
        conn.close()
    logger.info("[%s] 知識保存: [%s] %s", source_persona, category, content[:50])


def sk_get(limit: int = 50) -> list[dict]:
    """共有知識を取得。"""
    with _sk_lock:
        conn = _sk_connect()
        rows = conn.execute(
            "SELECT category, content, confidence, source_persona FROM knowledge "
            "WHERE is_active=1 ORDER BY updated_at DESC LIMIT ?",
            (limit,)).fetchall()
        conn.close()
    return [{"category": r[0], "content": r[1], "confidence": r[2],
             "source_persona": r[3]} for r in rows]


def sk_deactivate(content_match: str) -> int:
    """知識を無効化（完全一致）。"""
    with _sk_lock:
        conn = _sk_connect()
        cur = conn.execute(
            "UPDATE knowledge SET is_active=0 WHERE content=? AND is_active=1",
            (content_match,))
        conn.commit()
        count = cur.rowcount
        conn.close()
    return count
