from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

# actions_queue は DB 操作の実体
from actions_queue import enqueue_actions, lease_next_action, mark_done, mark_failed

router = APIRouter(prefix="/actions", tags=["worker-actions"])

# ★ここが重要：/actions 系は assistant.sqlite を正本に固定（DB分裂を止める）
FORCE_DB_PATH = "/srv/ritsu/state/assistant.sqlite"


def _db_path() -> str:
    # env で上書きしたいならここを getenv に変える
    return FORCE_DB_PATH


class DoneIn(BaseModel):
    action_id: int = Field(..., ge=1)


class FailedIn(BaseModel):
    action_id: int = Field(..., ge=1)
    error: str = ""


class EnqueueIn(BaseModel):
    actions: List[Dict[str, Any]] = Field(default_factory=list)


@router.get("/next")
def actions_next(worker_id: str = Query("default")) -> Dict[str, Optional[Dict[str, Any]]]:
    try:
        item = lease_next_action(worker_id=worker_id, db_path=_db_path())
        return {"item": item}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"actions_next failed: {e}")


@router.post("/done")
def actions_done(body: DoneIn) -> Dict[str, Any]:
    try:
        mark_done(body.action_id, db_path=_db_path())
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"actions_done failed: {e}")


@router.post("/failed")
def actions_failed(body: FailedIn) -> Dict[str, Any]:
    try:
        mark_failed(body.action_id, body.error or "", db_path=_db_path())
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"actions_failed failed: {e}")


@router.post("/enqueue")
def actions_enqueue(body: EnqueueIn) -> Dict[str, Any]:
    try:
        ids = enqueue_actions(body.actions, db_path=_db_path())
        return {"ok": True, "ids": ids}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"actions_enqueue failed: {e}")


@router.get("/debug")
def actions_debug() -> Dict[str, Any]:
    db_path = _db_path()
    counts: List[Dict[str, Any]] = []
    try:
        con = sqlite3.connect(db_path, timeout=5, isolation_level=None)
        try:
            rows = con.execute(
                "select state, count(*) as n from queued_actions group by state order by n desc"
            ).fetchall()
            counts = [{"state": r[0], "n": int(r[1])} for r in rows]
        finally:
            con.close()
    except Exception as e:
        return {"db_path": db_path, "error": str(e), "counts": counts}

    return {"db_path": db_path, "counts": counts}
