from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_DIR = Path(os.getenv("RITSU_STATE_DIR", "/srv/ritsu/state"))

# ★分裂防止：Phase5 の正本は assistant.sqlite の queued_actions に統一（env は見ない）
DEFAULT_DB_PATH = str(STATE_DIR / "assistant.sqlite")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS queued_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT,
  request_id TEXT,
  action_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,

  state TEXT NOT NULL DEFAULT 'queued', -- queued/running/done/failed
  priority INTEGER NOT NULL DEFAULT 100,
  retries INTEGER NOT NULL DEFAULT 0,
  retries_max INTEGER NOT NULL DEFAULT 3,
  next_run_at TEXT NOT NULL DEFAULT (datetime('now')),

  locked_by TEXT,
  locked_at TEXT,
  started_at TEXT,
  finished_at TEXT,

  last_error TEXT,
  last_error_at TEXT,

  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_qa_pick
ON queued_actions(state, next_run_at, priority, id);

CREATE INDEX IF NOT EXISTS idx_qa_req
ON queued_actions(request_id);
"""

def _connect(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(CREATE_SQL)
    return con


def enqueue_actions(
    actions: List[Dict[str, Any]],
    db_path: str = DEFAULT_DB_PATH,
    conversation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    priority: int = 100,
) -> List[int]:
    """
    actions: [{"type":"emotion","tag":"happy",...}, ...]
    payload_json: type以外を格納
    """
    ids: List[int] = []
    if not actions:
        return ids

    con = _connect(db_path)
    with con:
        for a in actions:
            if not isinstance(a, dict):
                continue
            action_type = str(a.get("type") or a.get("action_type") or "unknown")
            payload = {k: v for k, v in a.items() if k not in ("type", "action_type")}
            payload_json = json.dumps(payload, ensure_ascii=False)

            cur = con.execute(
                """
                INSERT INTO queued_actions(
                  conversation_id, request_id, action_type, payload_json,
                  state, priority, retries, retries_max, next_run_at,
                  created_at, updated_at
                ) VALUES (
                  ?, ?, ?, ?,
                  'queued', ?, 0, 3, datetime('now'),
                  datetime('now'), datetime('now')
                )
                """,
                (conversation_id, request_id, action_type, payload_json, int(priority)),
            )
            ids.append(int(cur.lastrowid))
    return ids


def _requeue_stale_running(con: sqlite3.Connection, lease_sec: float) -> int:
    # running が一定時間以上放置なら queued に戻す（ゾンビ解放）
    sec = max(1, int(lease_sec))
    cur = con.execute(
        """
        UPDATE queued_actions
        SET state='queued',
            locked_by=NULL,
            locked_at=NULL,
            updated_at=datetime('now')
        WHERE state='running'
          AND locked_at IS NOT NULL
          AND locked_at < datetime('now', ?)
        """,
        (f"-{sec} seconds",),
    )
    return int(cur.rowcount)


def lease_next_action(
    worker_id: str,
    db_path: str = DEFAULT_DB_PATH,
    lease_sec: float = 30.0,
) -> Optional[Dict[str, Any]]:
    con = _connect(db_path)
    with con:
        _requeue_stale_running(con, lease_sec)

        row = con.execute(
            """
            SELECT id, conversation_id, request_id, action_type, payload_json
            FROM queued_actions
            WHERE state='queued'
              AND next_run_at <= datetime('now')
            ORDER BY priority ASC, id ASC
            LIMIT 1
            """
        ).fetchone()

        if row is None:
            return None

        action_id = int(row["id"])
        con.execute(
            """
            UPDATE queued_actions
            SET state='running',
                locked_by=?,
                locked_at=datetime('now'),
                started_at=COALESCE(started_at, datetime('now')),
                updated_at=datetime('now')
            WHERE id=?
            """,
            (worker_id, action_id),
        )

    # payload を組み立て（type を復元）
    try:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        if not isinstance(payload, dict):
            payload = {"data": payload}
    except Exception:
        payload = {"raw": row["payload_json"]}

    payload.setdefault("type", row["action_type"])
    payload["action_id"] = action_id
    payload["id"] = action_id
    if row["conversation_id"]:
        payload.setdefault("conversation_id", row["conversation_id"])
    if row["request_id"]:
        payload.setdefault("request_id", row["request_id"])
    return payload


def mark_done(action_id: int, db_path: str = DEFAULT_DB_PATH) -> None:
    con = _connect(db_path)
    with con:
        con.execute(
            """
            UPDATE queued_actions
            SET state='done',
                finished_at=datetime('now'),
                locked_by=NULL,
                locked_at=NULL,
                updated_at=datetime('now')
            WHERE id=?
            """,
            (int(action_id),),
        )


def mark_failed(action_id: int, error: str = "", db_path: str = DEFAULT_DB_PATH) -> None:
    con = _connect(db_path)
    err = (error or "")[:2000]
    with con:
        row = con.execute(
            "SELECT retries, retries_max FROM queued_actions WHERE id=?",
            (int(action_id),),
        ).fetchone()
        if row is None:
            return

        retries = int(row["retries"] or 0) + 1
        retries_max = int(row["retries_max"] or 0)

        if retries <= retries_max:
            con.execute(
                """
                UPDATE queued_actions
                SET state='queued',
                    retries=?,
                    next_run_at=datetime('now', '+10 seconds'),
                    last_error=?,
                    last_error_at=datetime('now'),
                    locked_by=NULL,
                    locked_at=NULL,
                    updated_at=datetime('now')
                WHERE id=?
                """,
                (retries, err, int(action_id)),
            )
        else:
            con.execute(
                """
                UPDATE queued_actions
                SET state='failed',
                    retries=?,
                    finished_at=datetime('now'),
                    last_error=?,
                    last_error_at=datetime('now'),
                    locked_by=NULL,
                    locked_at=NULL,
                    updated_at=datetime('now')
                WHERE id=?
                """,
                (retries, err, int(action_id)),
            )
