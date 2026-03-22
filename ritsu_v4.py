#!/usr/bin/env python3
"""律 Aide V4 — Windows完結・API最小・1ファイル常駐AIアシスタント (Phase 1+2)"""

import ctypes
import ctypes.wintypes
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
TTS_SPEAKER_STYLE_ID = env_int("RITSU_TTS_SPEAKER_STYLE_ID", 2)
TTS_CABLE_DEVICE = env("RITSU_TTS_CABLE_DEVICE")  # empty = disabled
WINDOW_GEOMETRY = env("RITSU_WINDOW_GEOMETRY", "480x380")
MAX_TURNS = env_int("RITSU_MAX_TURNS", 16)
CONVERSATION_ID = env("RITSU_CONVERSATION_ID", "default")
STT_MODEL = env("RITSU_STT_MODEL", "small")
STT_DEVICE = env("RITSU_STT_DEVICE", "auto")  # auto/cuda/cpu

# STT (faster-whisper)
STT_MODEL = env("RITSU_STT_MODEL", "small")
STT_DEVICE = env("RITSU_STT_DEVICE", "auto")  # auto/cuda/cpu
STT_SAMPLE_RATE = 16000
STT_CHANNELS = 1

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

# ---------------------------------------------------------------------------
# 8. PTT (Push-to-Talk) recording
# ---------------------------------------------------------------------------

class PTTRecorder:
    """Records audio while PTT is held, then transcribes on release."""

    def __init__(self, on_result=None, on_status=None):
        self.on_result = on_result    # callback(text: str)
        self.on_status = on_status    # callback(status: str)
        self._recording = False
        self._frames: list = []
        self._stream = None
        self._lock = threading.Lock()

    def start(self):
        """Start recording."""
        import sounddevice as sd
        with self._lock:
            if self._recording:
                return
            self._recording = True
            self._frames = []
        if self.on_status:
            self.on_status("録音中…")
        log.info("PTT recording started (default input: %s)", sd.query_devices(kind='input')['name'])
        try:
            self._stream = sd.InputStream(
                samplerate=STT_SAMPLE_RATE,
                channels=STT_CHANNELS,
                dtype="float32",
                callback=self._audio_callback,
                blocksize=1024,
            )
            self._stream.start()
        except Exception as e:
            log.error("PTT start failed: %s", e)
            self._recording = False

    def stop(self):
        """Stop recording and transcribe."""
        import numpy as np
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        # Give stream a moment to flush final callbacks
        time.sleep(0.15)
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        frames = self._frames
        self._frames = []
        log.info("PTT stop: %d frames captured", len(frames))
        if not frames:
            log.warning("PTT: no audio captured")
            if self.on_status:
                self.on_status("音声なし")
            return
        audio = np.concatenate(frames, axis=0).flatten()
        duration = len(audio) / STT_SAMPLE_RATE
        log.info("PTT recording stopped: %.1fs audio", duration)
        if duration < 0.3:
            log.warning("PTT: too short (%.1fs), ignoring", duration)
            if self.on_status:
                self.on_status("短すぎます")
            return
        if self.on_status:
            self.on_status("認識中…")
        # Transcribe in background
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            log.warning("PTT audio callback status: %s", status)
        if self._recording:
            self._frames.append(indata.copy())

    def _transcribe(self, audio):
        text = stt_transcribe(audio, STT_SAMPLE_RATE)
        if text:
            log.info("PTT transcribed: %s", text)
            if self.on_status:
                self.on_status(f"認識: {text[:30]}")
            if self.on_result:
                self.on_result(text)
        else:
            log.warning("PTT: empty transcription")
            if self.on_status:
                self.on_status("認識失敗")

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
        tts_speak(reply)

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
        on_ptt_stop=lambda: threading.Thread(target=_gui_ptt.stop, daemon=True).start(),
    )

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
    log.info("律 Aide V4 starting (Phase 1+2)")

    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY is not set. Claude API calls will fail.")

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
