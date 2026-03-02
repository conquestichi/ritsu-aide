from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel, Field
from prometheus_client import CollectorRegistry, Counter, generate_latest, CONTENT_TYPE_LATEST

# OpenAI (Responses API)
from openai import OpenAI

# Long-term memory
try:
    import ritsu_memory as mem
    MEMORY_ENABLED = True
    print("[BOOT] ritsu_memory loaded")
except Exception as _mem_err:
    mem = None  # type: ignore
    MEMORY_ENABLED = False
    print(f"[WARN] ritsu_memory not loaded: {_mem_err}")

APP_NAME = "ritsu"

STATE_DIR = Path(os.getenv("RITSU_STATE_DIR", "/srv/ritsu/state"))
MEMORY_JSON = STATE_DIR / "memory.json"
# Phase5: queued_actions の正本は assistant.sqlite に固定
SQLITE_PATH = STATE_DIR / "assistant.sqlite"
os.environ["RITSU_ACTIONS_DB_PATH"] = str(SQLITE_PATH)



def _enqueue_into_queued_actions(actions: list, conversation_id: str, request_id: str) -> int:
    """
    Insert actions into assistant.sqlite:queued_actions (the same queue that /actions/next reads).
    payload_json stores action fields except "type".
    """
    if not actions:
        return 0

    def _to_dict(x):
        if x is None:
            return None
        if isinstance(x, dict):
            return x
        # pydantic v2
        if hasattr(x, "model_dump"):
            try:
                return x.model_dump()
            except Exception:
                pass
        # pydantic v1
        if hasattr(x, "dict"):
            try:
                return x.dict()
            except Exception:
                pass
        return None

    db_path = str(SQLITE_PATH)  # /srv/ritsu/state/assistant.sqlite
    con = sqlite3.connect(db_path, timeout=30)
    try:
        cur = con.cursor()
        n = 0
        for raw in actions:
            a = _to_dict(raw)
            if not a:
                continue

            action_type = str(a.get("type") or a.get("action_type") or "").strip()
            if not action_type:
                continue

            payload = {k: v for k, v in a.items() if k not in ("type", "action_type")}
            payload_json = json.dumps(payload, ensure_ascii=False)

            cur.execute(
                "INSERT INTO queued_actions (conversation_id, request_id, action_type, payload_json) "
                "VALUES (?,?,?,?)",
                (conversation_id, request_id, action_type, payload_json),
            )
            n += 1

        con.commit()
        return n
    finally:
        con.close()


DEFAULT_MODEL = os.getenv("RITSU_LLM_MODEL", "gpt-5.2")  # 好みで変更
MAX_TURNS = int(os.getenv("RITSU_SHORT_STACK_TURNS", "16"))  # user+assistant想定で *2 を保持
MAX_TEXT_LEN = int(os.getenv("RITSU_MAX_TEXT_LEN", "10000"))

from fastapi.responses import JSONResponse

class JSONUTF8Response(JSONResponse):
    media_type = "application/json; charset=utf-8"

app = FastAPI(default_response_class=JSONUTF8Response)


# --- worker actions routes (/actions/next, /actions/done, /actions/failed) ---
try:
    import worker_actions as _wa
    if hasattr(_wa, "router"):
        app.include_router(_wa.router)
        print("[BOOT] worker_actions router included")
    elif hasattr(_wa, "register"):
        _wa.register(app)
        print("[BOOT] worker_actions register(app) done")
    else:
        print("[WARN] worker_actions loaded but no router/register found")
except Exception as e:
    print(f"[WARN] worker_actions import failed: {e}")

# --- memory routes (/memory/knowledge, /memory/summaries, etc.) ---
if MEMORY_ENABLED and mem is not None:
    try:
        app.include_router(mem.memory_router)
        print("[BOOT] memory_router included")
    except Exception as e:
        print(f"[WARN] memory_router failed: {e}")

from fastapi import Request
from fastapi.responses import JSONResponse
try:
    from actions_queue import enqueue_actions, DEFAULT_DB_PATH
except Exception:
    enqueue_actions = None  # type: ignore
    DEFAULT_DB_PATH = "/srv/ritsu/state/assistant.sqlite"


RITSU_BEARER_TOKEN = os.getenv("RITSU_BEARER_TOKEN", "").strip()
AUTH_EXEMPT = {"/ready", "/health", "/metrics"}

@app.middleware("http")
async def bearer_auth(request: Request, call_next):
    if request.url.path in AUTH_EXEMPT:
        return await call_next(request)

    if not RITSU_BEARER_TOKEN:
        return JSONResponse({"detail": "server token not set"}, status_code=500)

    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {RITSU_BEARER_TOKEN}":
        return JSONResponse({"detail": "unauthorized"}, status_code=401)

    return await call_next(request)

# ---- metrics ----
REGISTRY = CollectorRegistry()
REQ_TOTAL = Counter(
    "ritsu_requests_total",
    "Total requests",
    ["path", "status"],
    registry=REGISTRY,
)

# ---- Models ----
class AssistantTextIn(BaseModel):
    conversation_id: str = Field(default="default", min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=MAX_TEXT_LEN)


EmotionTag = Literal["calm", "happy", "sorry", "warn", "think", "neutral"]


class AssistantTextOut(BaseModel):
    reply_text: str
    emotion_tag: EmotionTag


class ReplySchema(BaseModel):
    reply_text: str
    emotion_tag: EmotionTag


# ---- Persistence ----
DEFAULT_MEMORY: Dict[str, Any] = {
    "persona": {
        "name": "律",
        "role": "常駐秘書（司令官の実行補助）",
        "call_user": "司令官",
        "tone": "基本は落ち着いたプロ。短く結論から。癒し少し、ツンデレ軽め、たまにドジ要素。",
    },
    "style_rules": [
        "返答フォーマットは『結論→根拠→リスク/反証→次アクション』",
        "質問は最小。仮置きで進める（条件・前提・反証を添える）",
        "冗長・重複・ループを避ける",
        "秘匿情報を要求しない。保存しない",
        "出力は必ず JSON のみ：{ reply_text, emotion_tag }。余計なキー禁止",
    ],
    "micro_phrases": {
        "ack_ok": "了解。やる。",
        "ack_warn": "注意。ここは事故りやすい。",
        "ack_done": "OK、通った。",
    },
}


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_memory() -> Dict[str, Any]:
    """
    永続メモ（persona/rules等）。無ければ最小を生成して進む。
    """
    ensure_state_dir()
    if not MEMORY_JSON.exists():
        MEMORY_JSON.write_text(json.dumps(DEFAULT_MEMORY, ensure_ascii=False, indent=2), encoding="utf-8")
        return DEFAULT_MEMORY
    try:
        return json.loads(MEMORY_JSON.read_text(encoding="utf-8"))
    except Exception:
        # 壊れていても動かす（安全側）
        return DEFAULT_MEMORY


def db_connect() -> sqlite3.Connection:
    ensure_state_dir()
    con = sqlite3.connect(str(SQLITE_PATH))
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS turns (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          conversation_id TEXT NOT NULL,
          role TEXT NOT NULL,
          content TEXT NOT NULL,
          ts INTEGER NOT NULL
        );
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_turns_conv_ts ON turns(conversation_id, ts);"
    )
    return con


def db_get_recent_turns(conversation_id: str, limit: int) -> List[Dict[str, str]]:
    con = db_connect()
    try:
        cur = con.execute(
            """
            SELECT role, content
            FROM turns
            WHERE conversation_id = ?
            ORDER BY ts DESC, id DESC
            LIMIT ?
            """,
            (conversation_id, limit),
        )
        rows = cur.fetchall()
    finally:
        con.close()

    # DESC で取ってるので時系列に戻す
    rows.reverse()
    return [{"role": r, "content": c} for (r, c) in rows]


def db_add_turn(conversation_id: str, role: str, content: str) -> None:
    con = db_connect()
    try:
        con.execute(
            "INSERT INTO turns(conversation_id, role, content, ts) VALUES(?,?,?,?)",
            (conversation_id, role, content, int(time.time())),
        )

        # ざっくり：最新 MAX_TURNS*2（user+assistant想定）だけ残す
        keep = MAX_TURNS * 2
        con.execute(
            """
            DELETE FROM turns
            WHERE id IN (
              SELECT id FROM turns
              WHERE conversation_id = ?
              ORDER BY ts DESC, id DESC
              LIMIT -1 OFFSET ?
            )
            """,
            (conversation_id, keep),
        )
        con.commit()
    finally:
        con.close()


# ---- Prompt builder ----
def build_instructions(memory: Dict[str, Any], conversation_id: str = "") -> str:
    persona = memory.get("persona", {}) if isinstance(memory.get("persona", {}), dict) else {}
    name = persona.get("name", "律")
    role = persona.get("role", "常駐秘書（司令官の実行補助）")
    call_user = persona.get("call_user", "司令官")
    tone = persona.get("tone", "落ち着いたプロ。短く結論から。")

    style_rules = memory.get("style_rules", [])
    if not isinstance(style_rules, list) or not style_rules:
        style_rules = DEFAULT_MEMORY["style_rules"]

    micro = memory.get("micro_phrases", {})
    if not isinstance(micro, dict):
        micro = {}

    rules = "\n".join([f"- {r}" for r in style_rules])

    micro_blob = ""
    if micro:
        micro_blob = (
            "\n使ってもよい短い口癖候補（乱発禁止・必要時のみ）:\n"
            + json.dumps(micro, ensure_ascii=False)
        )

    base = (
        f"あなたは {name}。役割は {role}。\n"
        f"ユーザーの呼び名は「{call_user}」。\n"
        f"話し方: {tone}\n\n"
        f"厳守ルール:\n{rules}\n\n"
        "出力は必ず JSON のみ。\n"
        "形式: {\"reply_text\":\"...\", \"emotion_tag\":\"...\"} の2キーのみ。\n"
        "emotion_tag は calm/happy/sorry/warn/think/neutral のいずれか。\n"
        "JSON以外の文字、前置き、装飾、コードブロック、追加キーは禁止。\n"
        f"{micro_blob}"
    )

    # 長期記憶の注入
    if MEMORY_ENABLED and mem is not None and conversation_id:
        try:
            mem_ctx = mem.build_memory_context(conversation_id)
            if mem_ctx:
                base += mem_ctx
        except Exception as e:
            print(f"[WARN] memory context failed: {e}")

    return base


# ---- Routes ----
@app.get("/ready")
def ready():
    REQ_TOTAL.labels("/ready", "200").inc()
    return {"status": "ready"}


@app.get("/health")
def health():
    # readiness と同義にする（運用簡単優先）
    REQ_TOTAL.labels("/health", "200").inc()
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    REQ_TOTAL.labels("/metrics", "200").inc()
    return generate_latest(REGISTRY), 200, {"Content-Type": CONTENT_TYPE_LATEST}


def _safe_auto_memory(conversation_id: str, client) -> None:
    """Background task: run auto_process_memory without blocking the response."""
    try:
        mem.auto_process_memory(conversation_id, client)
    except Exception as e:
        print(f"[WARN] auto_process_memory: {e}")


@app.post("/assistant/text", response_model=AssistantTextOut)
def assistant_text(payload: AssistantTextIn, background_tasks: BackgroundTasks):
    REQ_TOTAL.labels("/assistant/text", "200").inc()

    # 記憶コマンド検出（"覚えて"/"忘れて"/"記憶一覧"）
    if MEMORY_ENABLED and mem is not None:
        cmd = mem.detect_memory_command(payload.text)
        if cmd:
            reply = mem.handle_memory_command(cmd)
            if reply:
                return AssistantTextOut(reply_text=reply, emotion_tag="calm")

    memory = load_memory()
    instructions = build_instructions(memory, conversation_id=payload.conversation_id)

    # short stack
    history = db_get_recent_turns(payload.conversation_id, limit=MAX_TURNS * 2)

    # OpenAI call (Structured Outputs)
    client = OpenAI()

    input_items = [{"role": "system", "content": instructions}]
    input_items.extend(history)
    input_items.append({"role": "user", "content": payload.text})

    try:
        resp = client.responses.parse(
            model=DEFAULT_MODEL,
            input=input_items,
            text_format=ReplySchema,  # pydantic schema
        )
        parsed: Optional[ReplySchema] = resp.output_parsed
        if not parsed:
            # 最悪でも形式維持
            parsed = ReplySchema(reply_text="(warn) 形式の解析に失敗。もう一度どうぞ。", emotion_tag="warn")

        # persist turns
        db_add_turn(payload.conversation_id, "user", payload.text)
        db_add_turn(payload.conversation_id, "assistant", parsed.reply_text)

        # 自動記憶処理（要約 + 知識抽出）— BackgroundTasks で非ブロッキング
        if MEMORY_ENABLED and mem is not None:
            background_tasks.add_task(_safe_auto_memory, payload.conversation_id, client)

        return AssistantTextOut(reply_text=parsed.reply_text, emotion_tag=parsed.emotion_tag)

    except Exception:
        # 連携側（TTS/ホットキー）を止めないため 200 で warn を返す
        return AssistantTextOut(
            reply_text="(warn) いま応答生成で失敗。少し間を置いて再送して。",
            emotion_tag="warn",
        )

# === V2 ASSISTANT ENDPOINT FIXED (phase5-min) ===
# 方針：
# - v2はHTTPループバックをやめて、同一プロセス内で assistant_text() を直接呼ぶ
# - emotion_tag から actions を自動生成して返す（必要なら actions_queue に enqueue）
import uuid
from fastapi import Request as FastAPIRequest
from pydantic import BaseModel, Field

_ALLOWED_EMOTION = {"calm", "happy", "sorry", "warn", "think", "neutral"}


class AssistantV2Request(BaseModel):
    conversation_id: str = "vm"
    text: str
    actions_in: Optional[List[Dict[str, Any]]] = None  # caller-supplied actions (E2E)


class AssistantV2Debug(BaseModel):
    request_id: str
    timings_ms: Dict[str, int] = Field(default_factory=dict)
    mode: str = "v2"
    warnings: List[str] = Field(default_factory=list)
    v1_status: int = 0


class AssistantV2Response(BaseModel):
    reply_text: str
    should_speak: bool = True
    emotion_tag: str = "neutral"
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    debug: AssistantV2Debug


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_emotion(tag: str) -> str:
    tag = (tag or "neutral").strip()
    return tag if tag in _ALLOWED_EMOTION else "neutral"


def _auto_emotion_actions(tag: str) -> List[Dict[str, Any]]:
    """
    Phase5-min:
      emotion_tag -> actions（最小仕様）
    ここは後で VMC/OSC 仕様に合わせて拡張する前提。
    """
    tag = _normalize_emotion(tag)
    if tag == "neutral":
        return []
    return [{"type": "emotion", "tag": tag, "source": "assistant_v2"}]


def _try_enqueue(actions: List[Dict[str, Any]], warnings: List[str]) -> None:
    if not actions:
        return
    if enqueue_actions is None:
        warnings.append("actions_queue_unavailable")
        return

    # DBパスは actions_queue 側の DEFAULT_DB_PATH を尊重（環境変数があれば優先）
    db_path = os.getenv("RITSU_ACTIONS_DB_PATH", DEFAULT_DB_PATH)
    try:
        # enqueue_actions(actions, db_path=...) 形式を優先
        enqueue_actions(actions, db_path=db_path)  # type: ignore[arg-type]
    except TypeError:
        # 旧シグネチャ enqueue_actions(actions) の場合
        try:
            enqueue_actions(actions)  # type: ignore[misc]
        except Exception:
            warnings.append("enqueue_failed")
    except Exception:
        warnings.append("enqueue_failed")


@app.post("/assistant/v2", response_model=AssistantV2Response)
def assistant_v2(req: AssistantV2Request, request: FastAPIRequest):
    request_id = str(uuid.uuid4())
    t0 = _now_ms()
    warnings: List[str] = []

    # ---- call v1 (direct) ----
    t1 = _now_ms()
    v1_status = 200
    try:
        v1_in = AssistantTextIn(conversation_id=req.conversation_id, text=req.text)
        v1_out = assistant_text(v1_in)  # 直接呼び出し（HTTPにしない）
        reply_text = getattr(v1_out, "reply_text", "")
        emotion_tag = getattr(v1_out, "emotion_tag", "neutral")
    except Exception:
        # 連携側（TTS/ホットキー）を止めないため、200でwarn返しの思想は維持
        reply_text = "(warn) いま応答生成で失敗。少し間を置いて再送して。"
        emotion_tag = "warn"
        v1_status = 500
        warnings.append("v1_exception")
    t2 = _now_ms()

    emotion_tag = _normalize_emotion(emotion_tag)
    if emotion_tag == "neutral":
        # 何もせずOK（無表情）
        pass

    # ---- build actions ----
    actions: List[Dict[str, Any]] = []
    if req.actions_in:
        actions.extend(req.actions_in)
    actions.extend(_auto_emotion_actions(emotion_tag))

    # ---- enqueue (optional) ----
    #_try_enqueue(actions, warnings)

    # ---- response ----
    t_end = _now_ms()
    dbg = AssistantV2Debug(
        request_id=request_id,
        timings_ms={
            "total": t_end - t0,
            "call_v1": t2 - t1,
        },
        mode="v2",
        warnings=warnings,
        v1_status=v1_status,
    )
    # --- Phase5: enqueue actions into queued_actions ---
    try:
        enq_n = _enqueue_into_queued_actions(actions, req.conversation_id, request_id)
        if enq_n:
            warnings.append(f"enqueued:{enq_n}")
    except Exception as e:
        warnings.append(f"enqueue_failed:{type(e).__name__}:{e}")


    return AssistantV2Response(
        reply_text=reply_text,
        should_speak=bool(reply_text.strip()),
        emotion_tag=emotion_tag,
        actions=actions,
        debug=dbg,
    )

