#!/usr/bin/env python3
"""律 Aide V4 — Windows完結・API最小・1ファイル常駐AIアシスタント (Phase 1)"""

import ctypes
import io
import json
import logging
import os
import queue
import re
import socket
import struct
import sys
import threading
import time
import traceback
import wave
from datetime import datetime
from pathlib import Path

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
TTS_SPEAKER_STYLE_ID = env_int("RITSU_TTS_SPEAKER_STYLE_ID", 2)
TTS_CABLE_DEVICE = env("RITSU_TTS_CABLE_DEVICE")  # empty = disabled
WINDOW_GEOMETRY = env("RITSU_WINDOW_GEOMETRY", "480x380")
MAX_TURNS = env_int("RITSU_MAX_TURNS", 16)
CONVERSATION_ID = env("RITSU_CONVERSATION_ID", "default")

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
# 4. Persona & system prompt
# ---------------------------------------------------------------------------

PERSONA_NAME = "律"
PERSONA_CALL_USER = "司令官"
PERSONA_TONE = "基本は落ち着いたプロ。短く結論から。癒し少し、ツンデレ軽め、たまにドジ要素。"

SYSTEM_PROMPT = f"""あなたは「{PERSONA_NAME}」。{PERSONA_CALL_USER}の常駐秘書AIアシスタント。
性格: {PERSONA_TONE}

返答ルール:
- 返答フォーマットは「結論→根拠→リスク/反証→次アクション」
- 質問は最小。仮置きで進める
- 冗長・重複・ループを避ける
- 出力は必ず以下のJSONのみ（他テキスト禁止）:
  {{"reply_text": "応答テキスト", "emotion_tag": "calm|happy|sorry|warn|think|neutral"}}

emotion_tag は以下から選択: calm, happy, sorry, warn, think, neutral
"""

# ---------------------------------------------------------------------------
# 5. Claude API client
# ---------------------------------------------------------------------------

_conversation: list[dict] = []
_conv_lock = threading.Lock()

def _call_claude(user_text: str) -> dict:
    """Call Claude API and return {reply_text, emotion_tag}."""
    import anthropic  # lazy import

    if not ANTHROPIC_API_KEY:
        return {"reply_text": "APIキーが設定されていません。.envを確認してください。", "emotion_tag": "warn"}

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
        resp = client.messages.create(
            model=RITSU_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
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

# ---------------------------------------------------------------------------
# 6. VOICEVOX TTS
# ---------------------------------------------------------------------------

_tts_queue: queue.Queue = queue.Queue()

def _tts_worker():
    """Background thread: consume TTS queue, synthesize & play via sounddevice."""
    import numpy as np
    import requests
    import sounddevice as sd

    while True:
        text = _tts_queue.get()
        if text is None:
            break
        try:
            _speak_voicevox(text, requests, np, sd)
        except Exception as e:
            log.error("TTS error: %s", e)
        finally:
            _tts_queue.task_done()

def _speak_voicevox(text: str, requests, np, sd):
    """Synthesize text with VOICEVOX and play."""
    # Audio query
    r = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": TTS_SPEAKER_STYLE_ID},
        timeout=10,
    )
    r.raise_for_status()
    aq = r.json()

    # Synthesis
    r = requests.post(
        f"{VOICEVOX_URL}/synthesis",
        params={"speaker": TTS_SPEAKER_STYLE_ID},
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

def tts_speak(text: str):
    """Enqueue text for TTS playback."""
    _tts_queue.put(text)

# ---------------------------------------------------------------------------
# 7. Chat handler (combines Claude + TTS)
# ---------------------------------------------------------------------------

def handle_user_input(text: str, log_callback=None):
    """Process user input: call Claude, log result, speak reply."""
    text = text.strip()
    if not text:
        return

    if log_callback:
        log_callback(f"[{PERSONA_CALL_USER}] {text}")

    result = _call_claude(text)
    reply = result["reply_text"]
    emotion = result["emotion_tag"]
    elapsed = result.get("elapsed", 0)

    tag = f" [{emotion}]" if emotion != "neutral" else ""
    time_str = f" ({elapsed:.1f}s)" if elapsed else ""

    if log_callback:
        log_callback(f"[{PERSONA_NAME}]{tag}{time_str} {reply}")

    tts_speak(reply)

# ---------------------------------------------------------------------------
# 8. tkinter GUI
# ---------------------------------------------------------------------------

def run_gui():
    """Main GUI loop (must run in main thread)."""
    import tkinter as tk
    from tkinter import scrolledtext

    root = tk.Tk()
    root.title(f"{PERSONA_NAME} Aide V4")
    geo = WINDOW_GEOMETRY
    root.geometry(geo)
    # topmost is set AFTER focus acquisition to avoid UIPI focus steal issues
    # --- Log area ---
    log_area = scrolledtext.ScrolledText(root, wrap=tk.WORD, state=tk.DISABLED,
                                          font=("Meiryo UI", 9), bg="#1e1e1e", fg="#d4d4d4",
                                          insertbackground="#d4d4d4")
    log_area.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 0))

    # --- Input frame ---
    input_frame = tk.Frame(root, bg="#2d2d2d")
    input_frame.pack(fill=tk.X, padx=4, pady=4)

    entry = tk.Entry(input_frame, font=("Meiryo UI", 10), bg="#3c3c3c", fg="#d4d4d4",
                     insertbackground="#d4d4d4", relief=tk.FLAT, takefocus=True)
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

    def append_log(msg: str):
        log_area.config(state=tk.NORMAL)
        log_area.insert(tk.END, msg + "\n")
        log_area.see(tk.END)
        log_area.config(state=tk.DISABLED)

    def on_send(event=None):
        text = entry.get().strip()
        if not text:
            return
        entry.delete(0, tk.END)
        threading.Thread(target=handle_user_input,
                         args=(text, lambda m: root.after(0, append_log, m)),
                         daemon=True).start()

    entry.bind("<Return>", on_send)

    send_btn = tk.Button(input_frame, text="送信", command=on_send,
                         font=("Meiryo UI", 9), bg="#0e639c", fg="white",
                         relief=tk.FLAT, padx=12)
    send_btn.pack(side=tk.RIGHT, padx=(4, 0))

    # Welcome message
    root.after(100, lambda: append_log(
        f"[{PERSONA_NAME}] V4起動完了。テキスト入力で会話できます。"))

    # Force focus on entry (topmost windows on Windows often lose keyboard focus)
    def _force_focus():
        root.deiconify()
        root.lift()
        root.focus_force()
        entry.focus_force()
        root.attributes("-topmost", True)
    root.after(300, _force_focus)
    root.after(600, lambda: entry.focus_force())

    # Click anywhere on window → focus entry
    root.bind("<Button-1>", lambda e: root.after(10, entry.focus_force))

    # DEBUG: log key events to console to diagnose input issues
    def _debug_key(e):
        print(f"[DEBUG] Key event on ROOT: keysym={e.keysym} char={repr(e.char)} widget={e.widget}")
    root.bind("<Key>", _debug_key)

    def _debug_entry_key(e):
        print(f"[DEBUG] Key event on ENTRY: keysym={e.keysym} char={repr(e.char)} state={e.state}")
    entry.bind("<Key>", _debug_entry_key, add="+")

    def _debug_focus(e):
        print(f"[DEBUG] FocusIn: widget={e.widget} class={e.widget.winfo_class()}")
    root.bind("<FocusIn>", _debug_focus, add="+")

    print(f"[DEBUG] entry.winfo_class()={entry.winfo_class()} takefocus={entry.cget('takefocus')}")
    print(f"[DEBUG] entry state={entry.cget('state')}")

    root.mainloop()

# ---------------------------------------------------------------------------
# 9. Main
# ---------------------------------------------------------------------------

def main():
    log.info("律 Aide V4 starting (Phase 1)")

    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY is not set. Claude API calls will fail.")

    # Start TTS worker
    tts_thread = threading.Thread(target=_tts_worker, daemon=True, name="TTS")
    tts_thread.start()

    # Run GUI (blocks in main thread)
    try:
        run_gui()
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        _tts_queue.put(None)  # signal TTS thread to exit

    log.info("律 Aide V4 stopped.")

if __name__ == "__main__":
    main()
