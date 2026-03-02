#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Ritsu Windows worker (notify + emotion/vmc_expression + gesture)

- Poll:   GET  /actions/next?worker_id=...
- Done:   POST /actions/done   {action_id}
- Failed: POST /actions/failed {action_id, error, retries, retries_max}

Phase4: Conditional Monologue Timer (Windows side)
- Conditions: idle_sec / time range / active window (fullscreen, title contains)
- Suppress: cooldown / max_per_day / stop_file
- Generate: POST /assistant/v2 (conversation_id fixed)
- Execute: local notify + local VMC expression (NOT enqueued)

Env (existing):
  RITSU_BASE_URL          (e.g. http://100.x.x.x:8181)  [required]
  RITSU_BEARER_TOKEN      [required]
  RITSU_WORKER_ID         (default: hostname)
  RITSU_POLL_SEC          (default: 1)
  RITSU_HTTP_TIMEOUT_SEC  (default: 10)
  RITSU_BACKOFF_SEC       (default: 3)

  # VMC (VMagicMirror VMCP receive)
  RITSU_VMC_HOST          (default: 127.0.0.1)
  RITSU_VMC_PORT          (default: 39539)
  RITSU_VMC_MAP_PATH      (default: ./vmc_expr_map.json)

  # Gesture Hotkeys (example: CTRL+ALT+F10)
  RITSU_GESTURE_NOD
  RITSU_GESTURE_WAVE
  RITSU_GESTURE_SHRUG

Env (Phase4 monologue):
  RITSU_MONOLOGUE_ENABLE=1
  RITSU_MONOLOGUE_IDLE_SEC=600
  RITSU_MONOLOGUE_COOLDOWN_SEC=900
  RITSU_MONOLOGUE_MAX_PER_DAY=20
  RITSU_MONOLOGUE_TIME_RANGE=08:00-23:00
  RITSU_MONOLOGUE_STOP_FILE=<path>   (default: %USERPROFILE%\.ritsu\monologue_stop.txt)
  RITSU_MONOLOGUE_TICK_SEC=5
  RITSU_MONOLOGUE_CONVERSATION_ID=monologue

  # active window suppression (optional)
  RITSU_MONOLOGUE_SUPPRESS_FULLSCREEN=1
  RITSU_MONOLOGUE_SUPPRESS_TITLE_CONTAINS=YouTube,Netflix,ゲーム,OBS

  # monologue expression behavior (optional)
  RITSU_MONOLOGUE_EXPR_HOLD_MS=900
  RITSU_MONOLOGUE_EXPR_FADE_MS=700

Notes:
- Logging is stdout only. Use run_ritsu_worker.cmd to redirect stdout to a file if desired.
"""

import os
import json
import time
import socket
import threading
import uuid
import re
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

import ctypes
import ctypes.wintypes

import requests
from pythonosc.udp_client import SimpleUDPClient

import sys

def _make_stdio_safe() -> None:
    # Keep current encoding (cp932 etc), just avoid crashing on unencodable chars.
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    try:
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass

_make_stdio_safe()

# gesture (optional)
try:
    import pyautogui  # pip install pyautogui
    pyautogui.FAILSAFE = False
except Exception:
    pyautogui = None


# -------------------------
# Basic helpers
# -------------------------

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def env(name: str, default: str = "") -> str:
    v = os.environ.get(name, default)
    return v.strip() if isinstance(v, str) else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except Exception:
        return default


def env_bool(name: str, default: bool) -> bool:
    v = env(name, "1" if default else "0").lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default


def expand_path(p: str) -> str:
    return os.path.expanduser(os.path.expandvars(p))


def default_stop_file() -> str:
    up = os.environ.get("USERPROFILE") or str(Path.home())
    return str(Path(up) / ".ritsu" / "monologue_stop.txt")


def ensure_parent(path_str: str) -> None:
    try:
        p = Path(path_str)
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


# -------------------------
# IO / HTTP
# -------------------------

def load_map(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as e:
        log(f"[WARN] map load failed: {path} err={e}")
        return {}


def post_json(session: requests.Session, url: str, headers: dict, payload: dict, timeout: float):
    return session.post(url, headers=headers, json=payload, timeout=timeout)


def get_json(session: requests.Session, url: str, headers: dict, timeout: float):
    return session.get(url, headers=headers, timeout=timeout)


# -------------------------
# Notify (best-effort)
# -------------------------

def notify_best_effort(title: str, text: str) -> None:
    # Try plyer
    try:
        from plyer import notification  # type: ignore
        notification.notify(title=title, message=text, timeout=10)
        return
    except Exception:
        pass

    # Try win10toast
    try:
        from win10toast import ToastNotifier  # type: ignore
        ToastNotifier().show_toast(title, text, duration=5, threaded=True)
        return
    except Exception:
        pass

    # Fallback: stdout only
    return


# -------------------------
# VMC expression
# -------------------------

_vmc_lock = threading.Lock()


def vmc_apply(client: SimpleUDPClient, name: str, value: float) -> None:
    # VMC Protocol (BlendShape Extension)
    # /VMC/Ext/Blend/Val   [name, float(value)]
    # /VMC/Ext/Blend/Apply []
    with _vmc_lock:
        client.send_message("/VMC/Ext/Blend/Val", [name, float(value)])
        client.send_message("/VMC/Ext/Blend/Apply", [])


def handle_vmc_expression(item: dict, vmc_client: SimpleUDPClient, expr_map: dict) -> None:
    payload = item.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    # payload supports:
    # 1) {"tag":"happy","hold_ms":900}
    # 2) {"name":"Happy","value":1.0,"hold_ms":900}
    tag = payload.get("tag") or payload.get("emotion") or payload.get("emotion_tag")
    name = payload.get("name") or payload.get("key")

    if tag and not name:
        name = expr_map.get(str(tag), "")

    if not name:
        log(f"[WARN] vmc_expression skip (no name). payload={payload}")
        return

    value = float(payload.get("value", 1.0))
    hold_ms = int(payload.get("hold_ms", 900))
    fade_ms = int(payload.get("fade_ms", 0))

    log(f"[VMC] name={name} value={value} hold_ms={hold_ms} fade_ms={fade_ms}")
    vmc_apply(vmc_client, name, value)

    time.sleep(max(hold_ms, 0) / 1000.0)

    if fade_ms and fade_ms > 0:
        steps = 5
        for i in range(steps - 1, -1, -1):
            v = value * (i / (steps - 1)) if steps > 1 else 0.0
            vmc_apply(vmc_client, name, v)
            time.sleep(fade_ms / 1000.0 / steps)
    else:
        vmc_apply(vmc_client, name, 0.0)


# -------------------------
# Notify action
# -------------------------

def handle_notify(item: dict) -> None:
    payload = item.get("payload") or {}
    text = payload.get("text") if isinstance(payload, dict) else str(payload)
    text = str(text or "")
    log(f"[NOTIFY] {text}")
    notify_best_effort("律", text)

# ------------------------
# Speak action
# ------------------------

def handle_speak(item: dict) -> None:
    """
    action.type == "speak"
    payload.text / payload.reply_text / payload.message などを読んで tts_speak.py に渡す
    """
    payload = item.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    text = (
        payload.get("text")
        or payload.get("reply_text")
        or payload.get("speak_text")
        or payload.get("message")
        or ""
    )
    text = str(text).strip()

    if not text:
        log("[SKIP] speak: empty text")
        return

    import os, sys, subprocess

    # このファイルと同じフォルダにある想定
    tts_py = os.path.join(os.path.dirname(__file__), "tts_speak.py")

    if not os.path.exists(tts_py):
        log(f"[SKIP] speak: tts_speak.py not found: {tts_py}")
        return

    try:
        # 1引数で渡す（tts_speak.py 側で sys.argv[1] を読む想定）
        subprocess.run([sys.executable, tts_py, text], check=False)
        log(f"[OK] speak executed len={len(text)}")
    except Exception as e:
        log(f"[FAILED] speak err={e}")

# -------------------------
# Gesture (hotkey)
# -------------------------


# --- TAG_REFINE_CALL_V1 BEGIN ---
try:
    _reply = (locals().get('reply_text') or locals().get('reply') or locals().get('assistant_text') or locals().get('text') or '')
    if 'emotion_tag' in locals():
        emotion_tag = refine_tag(emotion_tag, _reply)
    elif 'tag' in locals():
        tag = refine_tag(tag, _reply)
except Exception:
    pass
# --- TAG_REFINE_CALL_V1 END ---
def _vmc_emit_expr(emotion_tag: str, hold_ms: int) -> None:
    """Send expression to VMC/VMagicMirror based on tag."""
    try:
        import os, json, sys, subprocess, pathlib
        if os.getenv("RITSU_VMC_ENABLE", "0") != "1":
            return
        host = os.getenv("RITSU_VMC_HOST", "127.0.0.1")
        port = int(os.getenv("RITSU_VMC_PORT", "39539"))
        sender = os.getenv("RITSU_VMC_SENDER", os.path.join(os.path.dirname(__file__), "vmc_send_pyosc.py"))
        map_path = os.getenv("RITSU_VMC_MAP_PATH", os.path.join(os.path.dirname(__file__), "vmc_expr_map.json"))

        tag = (emotion_tag or "").strip().lower()
        if not tag:
            return

        try:
            mp = json.loads(pathlib.Path(map_path).read_text(encoding='utf-8-sig'))
        except Exception:
            return

        expr = mp.get(tag) or mp.get(tag.lower())
        if not expr:
            return

        hold_s = max(0.1, float(hold_ms)/1000.0)
        cmd = [sys.executable, sender, "--host", host, "--port", str(port),
               "--name", str(expr), "--value", "1.0", "--hold", str(hold_s)]
        subprocess.run(cmd, capture_output=True, text=True, timeout=2)
    except Exception:
        return

def parse_hotkey(spec: str) -> list[str]:
    # "CTRL+ALT+F10" -> ["ctrl","alt","f10"]
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    trans = {"control": "ctrl", "altgr": "alt", "escape": "esc", "return": "enter"}
    return [trans.get(p, p) for p in parts]


def hold_keys(keys: list[str], hold_ms: int) -> None:
    if not keys:
        return
    for k in keys:
        pyautogui.keyDown(k)
    time.sleep(max(hold_ms, 50) / 1000.0)
    for k in reversed(keys):
        pyautogui.keyUp(k)


def handle_gesture(item: dict) -> None:
    if pyautogui is None:
        log("[WARN] pyautogui not available; skip gesture")
        return

    payload = item.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    name = str(payload.get("name", "")).strip().lower()
    hold_ms = int(payload.get("hold_ms", 300))

    keymap = {
        "nod": env("RITSU_GESTURE_NOD", ""),
        "wave": env("RITSU_GESTURE_WAVE", ""),
        "shrug": env("RITSU_GESTURE_SHRUG", ""),
    }
    spec = keymap.get(name, "")

    if not spec:
        log(f"[WARN] gesture unknown/unmapped: name={name} (set RITSU_GESTURE_*)")
        return

    keys = parse_hotkey(spec)
    log(f"[GESTURE] name={name} hotkey={spec} keys={keys} hold_ms={hold_ms}")
    hold_keys(keys, hold_ms)


# -------------------------
# Phase4: Monologue timer
# -------------------------

# Idle detection (GetLastInputInfo)
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.UINT), ("dwTime", ctypes.wintypes.DWORD)]


def get_idle_seconds() -> int:
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0
    millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
    return int(millis / 1000)


# Foreground window title + fullscreen detection
user32 = ctypes.windll.user32
GetForegroundWindow = user32.GetForegroundWindow
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetWindowRect = user32.GetWindowRect
MonitorFromWindow = user32.MonitorFromWindow
GetMonitorInfoW = user32.GetMonitorInfoW
MONITOR_DEFAULTTONEAREST = 2


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.wintypes.LONG),
        ("top", ctypes.wintypes.LONG),
        ("right", ctypes.wintypes.LONG),
        ("bottom", ctypes.wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


def get_foreground_title() -> str:
    hwnd = GetForegroundWindow()
    if not hwnd:
        return ""
    length = GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or ""


def get_foreground_rect() -> RECT | None:
    hwnd = GetForegroundWindow()
    if not hwnd:
        return None
    rc = RECT()
    ok = GetWindowRect(hwnd, ctypes.byref(rc))
    return rc if ok else None


def get_monitor_rect_for_foreground() -> RECT | None:
    hwnd = GetForegroundWindow()
    if not hwnd:
        return None
    hmon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not hmon:
        return None
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    ok = GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi.rcMonitor if ok else None


def is_foreground_fullscreen(tolerance_px: int = 8) -> bool:
    wrc = get_foreground_rect()
    mrc = get_monitor_rect_for_foreground()
    if not wrc or not mrc:
        return False
    return (
        abs(wrc.left - mrc.left) <= tolerance_px
        and abs(wrc.top - mrc.top) <= tolerance_px
        and abs(wrc.right - mrc.right) <= tolerance_px
        and abs(wrc.bottom - mrc.bottom) <= tolerance_px
    )


def parse_time_range(spec: str) -> tuple[dtime, dtime]:
    # "HH:MM-HH:MM"
    m = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", (spec or "").strip())
    if not m:
        return dtime(8, 0), dtime(23, 0)
    sh, sm, eh, em = map(int, m.groups())
    return dtime(sh, sm), dtime(eh, em)


def in_time_range(start: dtime, end: dtime, now_dt: datetime) -> bool:
    t = now_dt.time()
    if start <= end:
        return start <= t <= end
    # crosses midnight
    return (t >= start) or (t <= end)


def monologue_state_path_today() -> str:
    up = os.environ.get("USERPROFILE") or str(Path.home())
    ymd = datetime.now().strftime("%Y%m%d")
    return str(Path(up) / ".ritsu" / f"monologue_state_{ymd}.json")


def load_daily_count() -> int:
    p = monologue_state_path_today()
    try:
        if not Path(p).exists():
            return 0
        data = json.loads(Path(p).read_text(encoding='utf-8-sig'))
        if isinstance(data, dict) and isinstance(data.get("count"), int):
            return int(data["count"])
    except Exception:
        return 0
    return 0


def save_daily_count(count: int) -> None:
    p = monologue_state_path_today()
    ensure_parent(p)
    try:
        Path(p).write_text(json.dumps({"count": int(count)}, ensure_ascii=False), encoding='utf-8-sig')
    except Exception:
        # best-effort; never crash
        pass


def normalize_emotion_to5(tag: str) -> str:
    t = (tag or "").strip().lower()
    if any(x in t for x in ("happy", "joy", "smile")):
        return "happy"
    if any(x in t for x in ("sad", "sorry", "sorrow", "apology")):
        return "sad"
    if any(x in t for x in ("angry", "warn", "mad")):
        return "angry"
    if any(x in t for x in ("surprise", "surprised", "shock")):
        return "surprised"
    return "neutral"


def build_monologue_prompt(idle_sec: int, title: str) -> str:
    now_hm = datetime.now().strftime("%H:%M")
    t = (title or "").strip()
    if len(t) > 80:
        t = t[:80] + "…"
    return (
        "【独り言モード】\n"
        f"- 現在時刻: {now_hm}\n"
        f"- 無操作: {idle_sec}秒\n"
        f"- アクティブウィンドウ: {t if t else '(不明)'}\n\n"
        "条件付きの独り言として、短く1〜2文だけ。作業の邪魔にならないトーンで。"
    )


def call_assistant_v2(
    session: requests.Session,
    base_url: str,
    headers: dict,
    timeout: float,
    conversation_id: str,
    text: str,
) -> tuple[str, bool, str]:
    url = f"{base_url.rstrip('/')}/assistant/v2"
    payload = {"conversation_id": conversation_id, "text": text, "actions_in": []}
    r = post_json(session, url, headers, payload, timeout)
    if r.status_code != 200:
        raise RuntimeError(f"assistant/v2 status={r.status_code} body={r.text[:200]}")
    data = r.json() if r.text else {}
    reply_text = str(data.get("reply_text", "") or "").strip()
    should_speak = bool(data.get("should_speak", True))
    emotion_tag = str(data.get("emotion_tag", "neutral") or "neutral")
    return reply_text, should_speak, emotion_tag

# =======================
# Phase4.6: Scheduled Monologue (fixed-time)
# =======================
import random
import threading

_SCHED_STARTED = False

def _sched_load_json(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except Exception:
        return {}

def _sched_load_state(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except Exception:
        return {"date": "", "fired": {}, "last_text": {}}

def _sched_save_state(path: str, st: dict) -> None:
    try:
        ensure_parent(path)
        Path(path).write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8-sig')
    except Exception:
        pass

def _sched_emit(text: str, emotion_tag: str, hold_ms: int) -> None:
    # まずログ（最低限これで動作確認できる）
    log(f"[SCHED] [NOTIFY] {text}  tag={emotion_tag}")

    # notify（既存の関数があれば呼ぶ）
    try:
        if "handle_notify_action" in globals():
            globals()["handle_notify_action"]({"text": text, "should_speak": True})
        elif "notify" in globals():
            globals()["notify"](text, True)
    except Exception as e:
        log(f"[SCHED] notify failed err={e}")

    # emotion（既存の関数があれば呼ぶ）
    try:
        if "handle_emotion_action" in globals():
            globals()["handle_emotion_action"]({"tag": emotion_tag, "hold_ms": int(hold_ms)})
        elif "vmc_apply_emotion" in globals():
            globals()["vmc_apply_emotion"](emotion_tag, int(hold_ms))
    except Exception as e:
        log(f"[SCHED] emotion failed err={e}")
def _start_scheduled_monologue_old_v1() -> None:
    global _SCHED_STARTED
    if _SCHED_STARTED:
        return

    enable = env_bool("RITSU_MONOLOGUE_SCHEDULE_ENABLE", True)
    if not enable:
        log("[SCHED] disabled (RITSU_MONOLOGUE_SCHEDULE_ENABLE!=1)")
        _SCHED_STARTED = True
        return

    schedule_path = expand_path(env("RITSU_MONOLOGUE_SCHEDULE_PATH", "%USERPROFILE%\\.ritsu\\monologue_schedule.json"))
    state_path = expand_path(env("RITSU_MONOLOGUE_SCHEDULE_STATE", "%LOCALAPPDATA%\\RitsuWorker\\monologue_schedule_state.json"))
    hold_ms = env_int("RITSU_MONOLOGUE_SCHEDULE_HOLD_MS", 1200)

    stop_file = expand_path(env("RITSU_MONOLOGUE_STOP_FILE", default_stop_file()))
    time_range = env("RITSU_MONOLOGUE_TIME_RANGE", "08:00-23:59")

    data = _sched_load_json(schedule_path)
    slots = data.get("slots") or []
    by_at = {}
    for s in slots:
        if isinstance(s, dict):
            at = str(s.get("at", "")).strip()
            if len(at) == 5 and at[2] == ":":
                by_at[at] = s

    st = _sched_load_state(state_path)
    rng = random.Random()

    log(f"[SCHED] enabled path={schedule_path} slots={len(by_at)} hold_ms={hold_ms}")

    def loop():
        nonlocal st
        while True:
            now_dt = datetime.now()
            hhmm = now_dt.strftime("%H:%M")
            today = now_dt.strftime("%Y-%m-%d")

            # 日付が変わったらリセット
            if st.get("date") != today:
                st = {"date": today, "fired": {}, "last_text": {}}
                _sched_save_state(state_path, st)

            # stop_file があれば停止
            if Path(stop_file).exists():
                time.sleep(1.0)
                continue

            # 時間帯ゲート（既存の in_time_range を利用）
            start_t, end_t = parse_time_range(time_range)
            if not in_time_range(start_t, end_t, now_dt):
                time.sleep(1.0)
                continue

            slot = by_at.get(hhmm)
            if slot and st.get("fired", {}).get(hhmm) != today:
                tag = str(slot.get("emotion_tag", "neutral") or "neutral")
                last = (st.get("last_text") or {}).get(hhmm)

                text = ""
                if isinstance(slot.get("text"), str):
                    text = slot["text"].strip()
                else:
                    vs = slot.get("variants") or []
                    vs = [v.strip() for v in vs if isinstance(v, str) and v.strip()]
                    if vs:
                        if last and len(vs) >= 2:
                            cand = [v for v in vs if v != last]
                            text = rng.choice(cand) if cand else rng.choice(vs)
                        else:
                            text = rng.choice(vs)

                if text:
                    _sched_emit(text, tag, hold_ms)
                    st.setdefault("fired", {})[hhmm] = today
                    st.setdefault("last_text", {})[hhmm] = text
                    _sched_save_state(state_path, st)

            time.sleep(1.0)

    _SCHED_STARTED = True
    threading.Thread(target=loop, name="ritsu_sched", daemon=True).start()


def monologue_loop(
    base_url: str,
    token: str,
    vmc_client: SimpleUDPClient,
    expr_map: dict,
    timeout: float,
) -> None:
    enable = env_bool("RITSU_MONOLOGUE_ENABLE", False)
    idle_need = env_int("RITSU_MONOLOGUE_IDLE_SEC", 600)
    cooldown = env_int("RITSU_MONOLOGUE_COOLDOWN_SEC", 900)
    max_per_day = env_int("RITSU_MONOLOGUE_MAX_PER_DAY", 20)
    time_range = env("RITSU_MONOLOGUE_TIME_RANGE", "08:00-23:00")
    tick_sec = env_int("RITSU_MONOLOGUE_TICK_SEC", 5)
    conv_id = env("RITSU_MONOLOGUE_CONVERSATION_ID", "monologue")
    require_idle_reset = env_bool("RITSU_MONOLOGUE_REQUIRE_IDLE_RESET", True)
    idle_reset_sec = env_int("RITSU_MONOLOGUE_IDLE_RESET_SEC", 2)

    suppress_fullscreen = env_bool("RITSU_MONOLOGUE_SUPPRESS_FULLSCREEN", True)
    suppress_title_contains = [x.strip().lower() for x in env("RITSU_MONOLOGUE_SUPPRESS_TITLE_CONTAINS", "").split(",") if x.strip()]

    stop_file = env("RITSU_MONOLOGUE_STOP_FILE", default_stop_file())
    stop_file = expand_path(stop_file)
    ensure_parent(stop_file)

    expr_hold_ms = env_int("RITSU_MONOLOGUE_EXPR_HOLD_MS", 900)
    expr_fade_ms = env_int("RITSU_MONOLOGUE_EXPR_FADE_MS", 700)

    start_t, end_t = parse_time_range(time_range)

    session = requests.Session()
    headers = {"Authorization": f"Bearer {token}"}

    count = load_daily_count()
    last_fire_ts = 0.0
    need_idle_reset = False

    last_skip_reason = ""
    last_skip_log_ts = 0.0

    log(
        "[BOOT] monologue start "
        f"enable={int(enable)} idle_sec={idle_need} cooldown_sec={cooldown} max_per_day={max_per_day} "
        f"time_range={time_range} stop_file={stop_file}"
    )


    start_scheduled_monologue()


    while True:
        try:
            enable = env_bool("RITSU_MONOLOGUE_ENABLE", False)
            if not enable:
                time.sleep(tick_sec)
                continue

            # stop file
            if Path(stop_file).exists():
                reason = "stop_file"
                ts = time.time()
                if reason != last_skip_reason or (ts - last_skip_log_ts) > 60:
                    log(f"[MONO] skip reason={reason} path={stop_file}")
                    last_skip_reason, last_skip_log_ts = reason, ts
                time.sleep(tick_sec)
                continue

            now_dt = datetime.now()

            # time range
            if not in_time_range(start_t, end_t, now_dt):
                reason = "time_range"
                ts = time.time()
                if reason != last_skip_reason or (ts - last_skip_log_ts) > 60:
                    log(f"[MONO] skip reason={reason} now={now_dt.strftime('%H:%M')} range={time_range}")
                    last_skip_reason, last_skip_log_ts = reason, ts
                time.sleep(tick_sec)
                continue

            # daily cap
            if max_per_day >= 0 and count >= max_per_day:
                reason = "max_per_day"
                ts = time.time()
                if reason != last_skip_reason or (ts - last_skip_log_ts) > 60:
                    log(f"[MONO] skip reason={reason} count={count} max={max_per_day}")
                    last_skip_reason, last_skip_log_ts = reason, ts
                time.sleep(tick_sec)
                continue

            # cooldown
            if cooldown > 0 and (time.time() - last_fire_ts) < cooldown:
                reason = "cooldown"
                ts = time.time()
                if reason != last_skip_reason or (ts - last_skip_log_ts) > 60:
                    left = int(cooldown - (time.time() - last_fire_ts))
                    log(f"[MONO] skip reason={reason} left_sec={max(left, 0)}")
                    last_skip_reason, last_skip_log_ts = reason, ts
                time.sleep(tick_sec)
                continue

            # idle
            # If enabled: after firing once, wait until user input resets idle before next fire.
            if require_idle_reset and need_idle_reset:
                idle_sec_now = get_idle_seconds()
                if idle_sec_now <= idle_reset_sec:
                    need_idle_reset = False  # user interacted, allow next idle-session fire
                else:
                    reason = "idle_reset"
                    ts = time.time()
                    if reason != last_skip_reason or (ts - last_skip_log_ts) > 60:
                        log(f"[MONO] skip reason={reason} idle={idle_sec_now} reset<={idle_reset_sec}")
                        last_skip_reason, last_skip_log_ts = reason, ts
                    time.sleep(tick_sec)
                    continue

            idle_sec = get_idle_seconds()
            if idle_sec < idle_need:
                reason = "idle"
                ts = time.time()
                if reason != last_skip_reason or (ts - last_skip_log_ts) > 60:
                    log(f"[MONO] skip reason={reason} idle={idle_sec} need>={idle_need}")
                    last_skip_reason, last_skip_log_ts = reason, ts
                time.sleep(tick_sec)
                continue

            # active window suppression
            title = get_foreground_title()

            if suppress_fullscreen and is_foreground_fullscreen():
                reason = "fullscreen"
                ts = time.time()
                if reason != last_skip_reason or (ts - last_skip_log_ts) > 60:
                    log(f"[MONO] skip reason={reason} title={title[:80]}")
                    last_skip_reason, last_skip_log_ts = reason, ts
                time.sleep(tick_sec)
                continue

            t_low = (title or "").lower()
            if suppress_title_contains and any(x in t_low for x in suppress_title_contains):
                reason = "active_window"
                ts = time.time()
                if reason != last_skip_reason or (ts - last_skip_log_ts) > 60:
                    log(f"[MONO] skip reason={reason} title={title[:80]}")
                    last_skip_reason, last_skip_log_ts = reason, ts
                time.sleep(tick_sec)
                continue

            # FIRE
            fire_id = str(uuid.uuid4())[:8]
            prompt = build_monologue_prompt(idle_sec=idle_sec, title=title)

            reply_text, should_speak, emotion_tag = call_assistant_v2(
                session=session,
                base_url=base_url,
                headers=headers,
                timeout=timeout,
                conversation_id=conv_id,
                text=prompt,
            )

            norm5 = normalize_emotion_to5(emotion_tag)

            log(f"[MONO] fire id={fire_id} tag={emotion_tag} norm={norm5} idle={idle_sec} should_speak={int(should_speak)}")

            if reply_text:
                # show
                log(f"[MONO] text={reply_text[:200]}")
                notify_best_effort("律（独り言）", reply_text)
            else:
                notify_best_effort("律（独り言）", "(empty reply)")

            # local expression (rounded to 5)
            payload = {
                "tag": norm5,
                "hold_ms": expr_hold_ms,
                "fade_ms": expr_fade_ms,
            }
            handle_vmc_expression({"payload": payload}, vmc_client, expr_map)

            last_fire_ts = time.time()
            need_idle_reset = True

            count += 1
            save_daily_count(count)

            last_skip_reason = ""
            last_skip_log_ts = 0.0

            time.sleep(tick_sec)

        except Exception as e:
            log(f"[ERR] monologue exception: {e}")
            time.sleep(3)


# -------------------------
# Main loop (existing)
# -------------------------

def main() -> int:
    base_url = env("RITSU_BASE_URL")
    token = env("RITSU_BEARER_TOKEN")
    worker_id = env("RITSU_WORKER_ID") or socket.gethostname()
    poll_sec = env_float("RITSU_POLL_SEC", 1.0)
    req_timeout = env_float("RITSU_HTTP_TIMEOUT_SEC", 10.0)
    backoff_sec = env_float("RITSU_BACKOFF_SEC", 3.0)

    # VMC target (VMagicMirror receive port)
    vmc_host = env("RITSU_VMC_HOST", "127.0.0.1")
    vmc_port = env_int("RITSU_VMC_PORT", 39539)
    map_path = env("RITSU_VMC_MAP_PATH", os.path.join(os.getcwd(), "vmc_expr_map.json"))

    if not base_url:
        log("[FATAL] RITSU_BASE_URL is empty")
        return 2
    if not token:
        log("[FATAL] RITSU_BEARER_TOKEN is empty")
        return 2

    base_url = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    def resolve_actions_base(session, base_url: str, headers: dict, req_timeout: float, worker_id: str) -> str:
        base = base_url.rstrip("/")
        candidates = [
            base,
            base + "/assistant",
        ]
        for cand in candidates:
            try:
                r = session.get(f"{cand}/actions/next?worker_id={worker_id}", headers=headers, timeout=req_timeout)
                if r.status_code != 404:
                    return cand
            except Exception:
                continue
        return base

    session = requests.Session()

    vmc_client = SimpleUDPClient(vmc_host, vmc_port)
    expr_map = load_map(map_path)

    # Ensure minimal 5 keys exist for Phase4 rounding
    for k in ("neutral", "happy", "sad", "angry", "surprised"):
        expr_map.setdefault(k, expr_map.get(k, ""))

    log(f"worker_id={worker_id}")
    log(f"base_url={base_url}")
    log(f"vmc={vmc_host}:{vmc_port} map={map_path}")

    actions_base = resolve_actions_base(session, base_url, headers, req_timeout, worker_id)
    log(f"actions_base={actions_base}")

    log("start loop (notify + emotion/vmc_expression + gesture)")

    # Start monologue thread (daemon). It self-disables if ENABLE=0.
    t = threading.Thread(
        target=monologue_loop,
        args=(base_url, token, vmc_client, expr_map, req_timeout),
        daemon=True,
    )
    t.start()

    while True:
        try:
            r = get_json(session, f"{actions_base}/actions/next?worker_id={worker_id}", headers, req_timeout)

            if r.status_code == 401:
                log("[ERR] 401 unauthorized (token mismatch/missing)")
                time.sleep(backoff_sec)
                continue

            if r.status_code != 200:
                log(f"[ERR] next status={r.status_code} body={r.text[:200]}")
                time.sleep(backoff_sec)
                continue

            data = r.json() if r.text else {}
            item = data.get("item")

            if not item:
                time.sleep(poll_sec)
                continue

            action_id = item.get("action_id") or item.get("id")
            a_type = item.get("type")
            log(f"[PICK] id={action_id} type={a_type}")

            try:
                if a_type == "notify":
                    handle_notify(item)
                elif a_type in ("emotion", "vmc_expression"):
                    handle_vmc_expression(item, vmc_client, expr_map)
                elif a_type == "gesture":
                    handle_gesture(item)
                elif a_type == "speak":
                    handle_speak(item)

                else:
                    log(f"[SKIP] unsupported type={a_type}")

                rr = post_json(session, f"{actions_base}/actions/done", headers, {"action_id": action_id}, req_timeout)

                log(f"[DONE] id={action_id} status={rr.status_code}")

            except Exception as e:
                log(f"[FAILED] id={action_id} err={e}")
                try:
                    post_json(session, f"{actions_base}/actions/failed", headers,
                             {"action_id": action_id, "error": str(e), "retries": 0, "retries_max": 0},
                             req_timeout)

                except Exception:
                    pass

        except KeyboardInterrupt:
            log("Ctrl+C -> exit")
            return 0
        except Exception as e:
            log(f"[ERR] loop exception: {e}")
            time.sleep(backoff_sec)

# =======================
# [SCHEDULE] 定期文言（固定時刻）エンジン
# =======================
import json
import os
import time
import random
import threading
import datetime as _dt

def _sched_now():
    return _dt.datetime.now()

def _sched_today_str():
    return _dt.date.today().isoformat()

def _sched_in_range(hhmm: str, range_str: str) -> bool:
    # range_str: "08:00-23:00"（既存のRITSU_MONOLOGUE_TIME_RANGEを流用）
    try:
        start, end = range_str.split("-", 1)
        def to_min(s: str) -> int:
            h, m = s.split(":")
            return int(h) * 60 + int(m)
        n = to_min(hhmm)
        a = to_min(start)
        b = to_min(end)
        if a <= b:
            return a <= n <= b
        # 例: 23:00-05:00 のような跨ぎ
        return (n >= a) or (n <= b)
    except Exception:
        return True

def _sched_load_json(path: str):
    with open(path, "r", encoding='utf-8-sig') as f:
        return json.load(f)

def _sched_write_json(path: str, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding='utf-8-sig') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def _sched_pick_text(slot: dict, rng: random.Random, last_text: str | None) -> str:
    if "text" in slot and isinstance(slot["text"], str):
        return slot["text"].strip()
    vs = slot.get("variants") or []
    vs = [v.strip() for v in vs if isinstance(v, str) and v.strip()]
    if not vs:
        return ""
    if last_text and len(vs) >= 2:
        # 直前と同じ文を避ける（同枠内）
        candidates = [v for v in vs if v != last_text]
        if candidates:
            return rng.choice(candidates)
    return rng.choice(vs)

def _sched_log(msg: str):
    # run_ritsu_worker.cmd が stdout をログに落としている前提なので print で統一
    print(msg, flush=True)

def _sched_fire_emit(text: str, emotion_tag: str, hold_ms: int):
    try:
        # --- TAG_REFINE_CALL_V1 BEGIN ---
        try:
            _reply = (locals().get('reply_text') or locals().get('reply') or locals().get('assistant_text') or locals().get('text') or '')
            if 'emotion_tag' in locals():
                emotion_tag = refine_tag(emotion_tag, _reply)
            elif 'tag' in locals():
                tag = refine_tag(tag, _reply)
        except Exception:
            pass
        # --- TAG_REFINE_CALL_V1 END ---
        _vmc_emit_expr(emotion_tag, hold_ms)
    except Exception as e:
        try:
            log(f"[WARN] vmc emit failed: {e}")
        except Exception:
            pass
    # 既存の独り言と同じ扱い：notifyログ＋（あれば）表情を適用
    _sched_log(f"[SCHED] [NOTIFY] {text}")
    # 既存実装に合わせて、表情は「emotionアクション形式」を呼べるなら呼ぶ
    # （関数名が違っても落ちないようにガード）
    try:
        # よくある形：handle_emotion_action({"tag":..., "hold_ms":...}) など
        if "handle_emotion_action" in globals():
            globals()["handle_emotion_action"]({"tag": emotion_tag, "hold_ms": hold_ms})
        elif "apply_emotion_tag" in globals():
            globals()["apply_emotion_tag"](emotion_tag, hold_ms)
        elif "vmc_apply_emotion" in globals():
            globals()["vmc_apply_emotion"](emotion_tag, hold_ms)
        else:
            _sched_log(f"[SCHED] [EMO] tag={emotion_tag} (no hook)")
    except Exception as e:
        _sched_log(f"[SCHED] [EMO] failed tag={emotion_tag} err={e}")
def start_scheduled_monologue():
    enable = os.environ.get("RITSU_MONOLOGUE_SCHEDULE_ENABLE", "1").strip() == "1"
    if not enable:
        _sched_log("[SCHED] disabled (RITSU_MONOLOGUE_SCHEDULE_ENABLE!=1)")
        return

    path = os.environ.get("RITSU_MONOLOGUE_SCHEDULE_PATH") or os.path.join(os.path.expanduser("~"), ".ritsu", "monologue_schedule.json")
    state_path = os.environ.get("RITSU_MONOLOGUE_SCHEDULE_STATE") or os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RitsuWorker", "monologue_schedule_state.json")
    hold_ms = int(os.environ.get("RITSU_MONOLOGUE_SCHEDULE_HOLD_MS", "1200"))
    stop_file = os.environ.get("RITSU_MONOLOGUE_STOP_FILE") or os.path.join(os.path.expanduser("~"), ".ritsu", "monologue_stop.txt")
    time_range = os.environ.get("RITSU_MONOLOGUE_TIME_RANGE", "00:00-23:59")
    suppress_fullscreen = os.environ.get("RITSU_MONOLOGUE_SUPPRESS_FULLSCREEN", "1").strip() == "1"

    try:
        data = _sched_load_json(path)
        slots = data.get("slots") or []
        slots_by_at = {}
        for s in slots:
            if not isinstance(s, dict): 
                continue
            at = str(s.get("at", "")).strip()
            if len(at) == 5 and at[2] == ":":
                slots_by_at[at] = s
        _sched_log(f"[SCHED] enabled path={path} slots={len(slots_by_at)} hold_ms={hold_ms}")
    except Exception as e:
        _sched_log(f"[SCHED] load failed path={path} err={e}")
        return

    # state
    state = {"date": _sched_today_str(), "fired": {}, "last_text": {}}
    try:
        if os.path.exists(state_path):
            state = _sched_load_json(state_path)
    except Exception:
        pass

    rng = random.Random()

    def _is_fullscreen_guard() -> bool:
        if not suppress_fullscreen:
            return False
        # 既存の fullscreen 判定があればそれを優先
        if "is_fullscreen_active" in globals():
            try:
                return bool(globals()["is_fullscreen_active"]())
            except Exception:
                return False
        return False

    def loop():
        while True:
            now = _sched_now()
            hhmm = now.strftime("%H:%M")

            # 日付が変わったらリセット
            today = _sched_today_str()
            if state.get("date") != today:
                state["date"] = today
                state["fired"] = {}
                state["last_text"] = {}
                try:
                    _sched_write_json(state_path, state)
                except Exception:
                    pass

            # stop_file
            if stop_file and os.path.exists(stop_file):
                time.sleep(2.0)
                continue

            # 時間帯ゲート（既存の monologue と揃える）
            if not _sched_in_range(hhmm, time_range):
                time.sleep(2.0)
                continue

            # フルスク抑制（既存があるなら使う）
            if _is_fullscreen_guard():
                time.sleep(2.0)
                continue

            slot = slots_by_at.get(hhmm)
            if slot:
                if state.get("fired", {}).get(hhmm) != today:
                    emotion_tag = str(slot.get("emotion_tag", "neutral")).strip() or "neutral"
                    last_text = (state.get("last_text") or {}).get(hhmm)
                    text = _sched_pick_text(slot, rng, last_text)
                    if text:
                        _sched_log(f"[SCHED] fire at={hhmm} tag={emotion_tag}")
                        _sched_fire_emit(text, emotion_tag, hold_ms)
                        state.setdefault("fired", {})[hhmm] = today
                        state.setdefault("last_text", {})[hhmm] = text
                        try:
                            _sched_write_json(state_path, state)
                        except Exception:
                            pass
            time.sleep(1.0)

    th = threading.Thread(target=loop, name="ritsu_schedule", daemon=True)
    th.start()



# === VMC_EMIT_HELPER_V2 BEGIN ===
# --- TAG_REFINE_CALL_V1 BEGIN ---
try:
    _reply = (locals().get('reply_text') or locals().get('reply') or locals().get('assistant_text') or locals().get('text') or '')
    if 'emotion_tag' in locals():
        emotion_tag = refine_tag(emotion_tag, _reply)
    elif 'tag' in locals():
        tag = refine_tag(tag, _reply)
except Exception:
    pass
# --- TAG_REFINE_CALL_V1 END ---
def _vmc_emit_expr(tag: str, hold_ms: int = 1500, host=None, port=None):
    """
    Send expression to VMC/VMagicMirror via OSC.
    tag: e.g. happy/sad/angry/calm...
    Map: RITSU_VMC_MAP_PATH or ./vmc_expr_map.json
    Host/Port: RITSU_VMC_HOST / RITSU_VMC_PORT (default 127.0.0.1:39539)
    """
    import os, json, time
    try:
        from pythonosc.udp_client import SimpleUDPClient
    except Exception as e:
        # python-osc missing
        if os.getenv("RITSU_VMC_DEBUG","0") == "1":
            print("[VMC][ERR] python-osc not available:", e)
        return

    host = host or os.getenv("RITSU_VMC_HOST", "127.0.0.1")
    port = int(port or os.getenv("RITSU_VMC_PORT", "39539"))
    mp = os.getenv("RITSU_VMC_MAP_PATH", os.path.join(os.path.dirname(__file__), "vmc_expr_map.json"))

    expr_map = {}
    try:
        with open(mp, "r", encoding='utf-8-sig') as f:
            expr_map = json.load(f) or {}
    except Exception:
        expr_map = {}

    k = (tag or "").strip()
    expr = expr_map.get(k.lower()) or expr_map.get(k) or "Neutral"
    val = float(os.getenv("RITSU_VMC_VALUE", "1.0"))
    hold = max(0.05, float(hold_ms)/1000.0)
    debug = os.getenv("RITSU_VMC_DEBUG", "0") == "1"

    try:
        c = SimpleUDPClient(host, port)
        c.send_message("/VMC/Ext/OK", 1)
        c.send_message("/VMC/Ext/Blend/Val", [expr, val])
        c.send_message("/VMC/Ext/Blend/Apply", [])
        if debug:
            print(f"[VMC] tag={k} expr={expr} host={host} port={port} hold={hold}s map={mp}")
        time.sleep(hold)
        c.send_message("/VMC/Ext/Blend/Val", [expr, 0.0])
        c.send_message("/VMC/Ext/Blend/Apply", [])
    except Exception as e:
        if debug:
            print("[VMC][ERR]", e)
# === VMC_EMIT_HELPER_V2 END ===

if __name__ == "__main__":
    raise SystemExit(main())

# --- TAG_REFINE_HELPER_V1 BEGIN ---
def refine_tag(tag: str, reply_text: str) -> str:
    """
    Convert coarse emotion tag into laugh_* tiers using reply text.
    """
    t = (tag or "").strip().lower()
    s = (reply_text or "")
    sl = s.lower()

    happyish = {"happy","joy","fun","laugh_weak","laugh","laugh_strong","smug","excited","shy"}
    if t in happyish:
        score = 0
        score += sl.count("w") + s.count("ｗ")
        score += 3 * s.count("笑")
        score += 2 * (s.count("はは") + s.count("哈哈"))
        score += 1 * (s.count("ふふ") + s.count("へへ") + s.count("^_^") + s.count("^^"))
        score += s.count("!") + s.count("！")
        if score >= 10: return "laugh_strong"
        if score >= 4:  return "laugh"
        if score >= 1:  return "laugh_weak"
        return "happy"

    if t in ("sad","sorrow"): return "sad"
    if t in ("sorry","apology","apologize"): return "sorry"
    if t in ("angry","mad","annoyed","warn"): return "angry"
    if t in ("surprised","surprise"): return "surprised"
    if t in ("calm","think","neutral",""): return "neutral"
    return t
# --- TAG_REFINE_HELPER_V1 END ---

