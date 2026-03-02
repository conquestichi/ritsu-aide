#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ritsu.py — 律 統合クライアント v3

旧20+ファイル(AHK/PS1/CMD/Python混在)を1プロセスに統合。

Architecture:
  [Main Thread: tkinter GUI]
       ↕ Queue
  [API Thread]      → VPS :8181 (HTTP direct)
  [Worker Thread]   → Action polling → VMC/TTS/Notify
  [Monologue Thread]→ Idle detect → /assistant/v2
  [Tunnel Thread]   → SSH tunnel management
  [TTS Thread]      → VOICEVOX synthesis + playback
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()

def _env_int(name: str, default: int) -> int:
    try: return int(_env(name))
    except: return default

def _env_float(name: str, default: float) -> float:
    try: return float(_env(name))
    except: return default

def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    if not v: return default
    return v.lower() in ("1", "true", "yes")

def _expand(p: str) -> str:
    return os.path.expandvars(os.path.expanduser(p))

def _load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^([^=]+?)=(.*)$', line)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            os.environ[k] = v

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_log_lock = threading.Lock()
_log_file: Optional[Path] = None

def _init_log():
    global _log_file
    d = Path(os.environ.get("LOCALAPPDATA", ".")) / "RitsuWorker"
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_file = d / f"ritsu_v3_{ts}.log"

def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with _log_lock:
        print(line, flush=True)
        if _log_file:
            try:
                with open(_log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except:
                pass

# ---------------------------------------------------------------------------
# SSH Tunnel
# ---------------------------------------------------------------------------
class SSHTunnel:
    """SSH tunnel: localhost:local_port → remote:remote_port"""

    def __init__(self, ssh_host: str, local_port: int = 18181,
                 remote_port: int = 8181, ssh_port: int = 22):
        self.ssh_host = ssh_host
        self.local_port = local_port
        self.remote_port = remote_port
        self.ssh_port = ssh_port
        self._proc: Optional[subprocess.Popen] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not self.ssh_host:
            log("[TUNNEL] disabled (no ssh_host)")
            return
        threading.Thread(target=self._run, daemon=True, name="ssh_tunnel").start()

    def _run(self) -> None:
        ssh = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                           "System32", "OpenSSH", "ssh.exe")
        if not os.path.exists(ssh):
            ssh = "ssh"
        forward = f"127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}"
        args = [
            ssh, "-4", "-N",
            "-L", forward,
            "-p", str(self.ssh_port),
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "TCPKeepAlive=yes",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
            self.ssh_host,
        ]
        while not self._stop.is_set():
            log(f"[TUNNEL] connecting {self.ssh_host}:{self.ssh_port} → localhost:{self.local_port}")
            try:
                self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.PIPE)
                self._proc.wait()
                rc = self._proc.returncode
                log(f"[TUNNEL] exited rc={rc}")
            except Exception as e:
                log(f"[TUNNEL] error: {e}")
            if not self._stop.is_set():
                time.sleep(5)

    def stop(self) -> None:
        self._stop.set()
        if self._proc:
            self._proc.terminate()

# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------
class RitsuAPI:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        })
        self.timeout = timeout

    def ready(self) -> bool:
        try:
            r = self.session.get(f"{self.base}/ready", timeout=3)
            return r.status_code == 200
        except:
            return False

    def send_text(self, text: str, conv_id: str = "gui") -> dict:
        """POST /assistant/text → {reply_text, emotion_tag}"""
        r = self.session.post(
            f"{self.base}/assistant/text",
            json={"conversation_id": conv_id, "text": text},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def send_v2(self, text: str, conv_id: str = "monologue") -> dict:
        """POST /assistant/v2 → {reply_text, emotion_tag, should_speak, ...}"""
        r = self.session.post(
            f"{self.base}/assistant/v2",
            json={"conversation_id": conv_id, "text": text, "actions_in": []},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def action_next(self, worker_id: str) -> Optional[dict]:
        """GET /actions/next?worker_id=..."""
        r = self.session.get(
            f"{self.base}/actions/next",
            params={"worker_id": worker_id},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("item") or data

    def action_done(self, action_id: str) -> None:
        self.session.post(
            f"{self.base}/actions/done",
            json={"action_id": action_id},
            timeout=self.timeout,
        )

    def action_failed(self, action_id: str, error: str) -> None:
        self.session.post(
            f"{self.base}/actions/failed",
            json={"action_id": action_id, "error": error,
                   "retries": 0, "retries_max": 0},
            timeout=self.timeout,
        )

# ---------------------------------------------------------------------------
# VMC Expression
# ---------------------------------------------------------------------------
class VMCClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 39539,
                 expr_map_path: str = ""):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._client = None
        self.expr_map: dict = {}
        self._load_map(expr_map_path)

    def _ensure_client(self):
        if self._client is None:
            try:
                from pythonosc.udp_client import SimpleUDPClient
                self._client = SimpleUDPClient(self.host, self.port)
            except ImportError:
                log("[VMC] pythonosc not installed")
                return None
        return self._client

    def _load_map(self, path: str):
        if path and os.path.isfile(path):
            try:
                self.expr_map = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            except:
                pass
        for k in ("neutral", "happy", "sad", "angry", "surprised", "calm", "warn", "think", "sorry"):
            self.expr_map.setdefault(k, self.expr_map.get(k, ""))

    def send_expression(self, tag: str, value: float = 1.0,
                        hold_ms: int = 900, fade_ms: int = 0) -> None:
        c = self._ensure_client()
        if not c:
            return
        name = self.expr_map.get(tag.lower(), "")
        if not name:
            return
        try:
            with self._lock:
                c.send_message("/VMC/Ext/Blend/Val", [name, float(value)])
                c.send_message("/VMC/Ext/Blend/Apply", [])
            time.sleep(max(hold_ms, 0) / 1000.0)
            if fade_ms > 0:
                steps = 5
                for i in range(steps - 1, -1, -1):
                    v = value * (i / max(steps - 1, 1))
                    with self._lock:
                        c.send_message("/VMC/Ext/Blend/Val", [name, float(v)])
                        c.send_message("/VMC/Ext/Blend/Apply", [])
                    time.sleep(fade_ms / 1000.0 / steps)
            else:
                with self._lock:
                    c.send_message("/VMC/Ext/Blend/Val", [name, 0.0])
                    c.send_message("/VMC/Ext/Blend/Apply", [])
        except Exception as e:
            log(f"[VMC] send failed: {e}")

# ---------------------------------------------------------------------------
# TTS (VOICEVOX)
# ---------------------------------------------------------------------------
class TTSEngine:
    PRESETS = {
        "amaama": {"speed": 0.98, "pitch": 0.00, "intonation": 1.20, "volume": 1.00},
        "sexy":   {"speed": 1.03, "pitch": 0.00, "intonation": 1.02, "volume": 0.95},
    }
    SEXY_WORDS = ("ねえ", "だよ", "かな", "お願い", "だめ", "すき", "好き", "おはよ", "おやすみ")

    def __init__(self, base_url: str = "http://127.0.0.1:50021",
                 speaker_name: str = "四国めたん"):
        self.base = base_url
        self.speaker_name = speaker_name
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="tts")
        self._thread.start()
        self._style_cache: dict = {}

    def speak(self, text: str) -> None:
        if text.strip():
            log(f"[TTS] queued: {text[:50]}...")
            self._queue.put(text.strip())

    def _worker(self) -> None:
        import urllib.request, urllib.parse
        log("[TTS] worker thread started")
        while True:
            text = self._queue.get()
            log(f"[TTS] processing: {text[:50]}...")
            try:
                chunks = self._split(text)
                for chunk, pause in chunks:
                    style = "sexy" if self._is_sexy(chunk) else "amaama"
                    preset = self.PRESETS[style]
                    style_name = "セクシー" if style == "sexy" else "あまあま"
                    sid = self._resolve_style(style_name)
                    log(f"[TTS] style={style_name} sid={sid}")
                    if sid is None:
                        log(f"[TTS] SKIP: could not resolve style '{style_name}'")
                        continue
                    # audio_query
                    q = urllib.parse.urlencode({"text": chunk, "speaker": sid})
                    req = urllib.request.Request(
                        f"{self.base}/audio_query?{q}", data=b"", method="POST")
                    aq = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
                    aq["speedScale"] = preset["speed"]
                    aq["pitchScale"] = preset["pitch"]
                    aq["intonationScale"] = preset["intonation"]
                    aq["volumeScale"] = preset["volume"]
                    # synthesis
                    body = json.dumps(aq, ensure_ascii=False).encode("utf-8")
                    req2 = urllib.request.Request(
                        f"{self.base}/synthesis?speaker={sid}",
                        data=body, headers={"Content-Type": "application/json"},
                        method="POST")
                    wav = urllib.request.urlopen(req2, timeout=60).read()
                    # play
                    tmp = Path(os.environ.get("TEMP", ".")) / "ritsu_tts_out.wav"
                    tmp.write_bytes(wav)
                    log(f"[TTS] playing {len(wav)} bytes")
                    import winsound
                    winsound.PlaySound(str(tmp), winsound.SND_FILENAME)
                    time.sleep(pause)
            except Exception as e:
                log(f"[TTS] error: {e}")

    def _resolve_style(self, style_name: str) -> Optional[int]:
        cache_key = f"{self.speaker_name}/{style_name}"
        if cache_key in self._style_cache:
            return self._style_cache[cache_key]
        try:
            import urllib.request
            raw = urllib.request.urlopen(f"{self.base}/speakers", timeout=3).read()
            speakers = json.loads(raw.decode("utf-8"))
            for sp in speakers:
                sp_name = sp.get("name", "")
                if sp_name == self.speaker_name or self.speaker_name in sp_name:
                    log(f"[TTS] found speaker: {sp_name}")
                    for st in sp.get("styles", []):
                        st_name = st.get("name", "")
                        log(f"[TTS]   style: '{st_name}' id={st.get('id')}")
                        if st_name == style_name:
                            sid = int(st["id"])
                            self._style_cache[cache_key] = sid
                            log(f"[TTS]   MATCH: {style_name} → {sid}")
                            return sid
                    # Fallback: first style
                    if sp.get("styles"):
                        sid = int(sp["styles"][0]["id"])
                        self._style_cache[cache_key] = sid
                        log(f"[TTS]   fallback to first style: {sid}")
                        return sid
            log(f"[TTS] speaker '{self.speaker_name}' not found in {len(speakers)} speakers")
        except Exception as e:
            log(f"[TTS] speaker resolve EXCEPTION: {e}")
        return None

    def _is_sexy(self, text: str) -> bool:
        t = text.strip()
        if not t: return False
        if any(w in t for w in self.SEXY_WORDS) and len(t) <= 28:
            return True
        if t.endswith(("…", "。", "？", "?")) and len(t) <= 22:
            return True
        return False

    def _split(self, text: str) -> list[tuple[str, float]]:
        parts, buf = [], []
        for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
            buf.append(ch)
            if ch in "。！!？?\n":
                p = "".join(buf).strip()
                if p: parts.append(p)
                buf = []
        tail = "".join(buf).strip()
        if tail: parts.append(tail)
        out = []
        for p in parts:
            p2 = p.replace("\n", " ").strip()
            if not p2: continue
            if p2.endswith(("。", "…")): pause = 0.45
            elif p2.endswith(("！", "!", "？", "?")): pause = 0.30
            else: pause = 0.15
            out.append((p2, pause))
        return out

# ---------------------------------------------------------------------------
# STT (OpenAI Whisper)
# ---------------------------------------------------------------------------
class STTEngine:
    def __init__(self):
        self._key: str = ""
        kp = Path.home() / ".ritsu" / "openai_key.txt"
        if kp.exists():
            self._key = kp.read_text(encoding="utf-8").strip()

    def record_and_transcribe(self, seconds: float = 5.0) -> str:
        if not self._key:
            return "(STT: openai_key.txt not found)"
        try:
            import sounddevice as sd
            import wave, tempfile
            rate = 16000
            frames = int(rate * seconds)
            audio = sd.rec(frames, samplerate=rate, channels=1, dtype="int16")
            sd.wait()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
                with wave.open(f, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(rate)
                    wf.writeframes(audio.tobytes())
            from openai import OpenAI
            client = OpenAI(api_key=self._key)
            with open(tmp, "rb") as af:
                t = client.audio.transcriptions.create(
                    model="gpt-4o-mini-transcribe", file=af)
            os.unlink(tmp)
            return getattr(t, "text", str(t)).strip()
        except Exception as e:
            return f"(STT error: {e})"

# ---------------------------------------------------------------------------
# PTT (Push-to-Talk) Recording
# ---------------------------------------------------------------------------
class PTTRecorder:
    def __init__(self):
        self._recording = False
        self._q: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._wav_path = str(Path(os.environ.get("TEMP", ".")) / "ritsu_ptt.wav")

    def start_recording(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._stop_event.clear()
        threading.Thread(target=self._record, daemon=True).start()
        log("[PTT] recording started")

    def stop_recording(self) -> Optional[str]:
        if not self._recording:
            return None
        self._stop_event.set()
        self._recording = False
        time.sleep(0.3)
        if os.path.exists(self._wav_path):
            log("[PTT] recording stopped")
            return self._wav_path
        return None

    def _record(self) -> None:
        try:
            import sounddevice as sd
            import soundfile as sf
            rate = 16000
            q: queue.Queue = queue.Queue()
            def cb(indata, frames, time_info, status):
                q.put(indata.copy())
            with sf.SoundFile(self._wav_path, mode="w", samplerate=rate,
                              channels=1, subtype="PCM_16") as f:
                with sd.InputStream(samplerate=rate, channels=1, dtype="int16", callback=cb):
                    while not self._stop_event.is_set():
                        try:
                            data = q.get(timeout=0.2)
                            f.write(data)
                        except:
                            continue
        except Exception as e:
            log(f"[PTT] record error: {e}")

# ---------------------------------------------------------------------------
# Windows Idle Detection
# ---------------------------------------------------------------------------
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.UINT), ("dwTime", ctypes.wintypes.DWORD)]

def get_idle_seconds() -> int:
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0
    return int((ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000)

def get_foreground_title() -> str:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd: return ""
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length <= 0: return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or ""

# ---------------------------------------------------------------------------
# Notify (Windows toast)
# ---------------------------------------------------------------------------
def notify(title: str, text: str) -> None:
    try:
        from plyer import notification
        notification.notify(title=title, message=text, timeout=10)
        return
    except: pass
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(title, text, duration=5, threaded=True)
    except: pass

# ---------------------------------------------------------------------------
# Worker Thread (action polling)
# ---------------------------------------------------------------------------
class WorkerThread(threading.Thread):
    def __init__(self, api: RitsuAPI, vmc: VMCClient, tts: TTSEngine,
                 worker_id: str, poll_sec: float = 1.0):
        super().__init__(daemon=True, name="worker")
        self.api = api
        self.vmc = vmc
        self.tts = tts
        self.worker_id = worker_id
        self.poll_sec = poll_sec

    def run(self):
        log(f"[WORKER] start worker_id={self.worker_id}")
        while True:
            try:
                item = self.api.action_next(self.worker_id)
                if not item or not item.get("type"):
                    time.sleep(self.poll_sec)
                    continue
                aid = item.get("action_id") or item.get("id", "")
                atype = item.get("type", "")
                log(f"[WORKER] pick id={aid} type={atype}")
                try:
                    self._dispatch(item)
                    self.api.action_done(aid)
                except Exception as e:
                    log(f"[WORKER] failed id={aid}: {e}")
                    self.api.action_failed(aid, str(e))
            except Exception as e:
                log(f"[WORKER] poll error: {e}")
                time.sleep(3)

    def _dispatch(self, item: dict):
        t = item.get("type", "")
        payload = item.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        if t == "notify":
            text = payload.get("text", str(payload))
            notify("律", str(text))
        elif t in ("emotion", "vmc_expression"):
            tag = payload.get("tag") or payload.get("emotion_tag", "neutral")
            hold = int(payload.get("hold_ms", 900))
            fade = int(payload.get("fade_ms", 0))
            self.vmc.send_expression(tag, hold_ms=hold, fade_ms=fade)
        elif t == "speak":
            text = payload.get("text") or payload.get("reply_text") or payload.get("message", "")
            if text:
                self.tts.speak(str(text))
        elif t == "gesture":
            log(f"[WORKER] gesture: {payload.get('name', '?')}")
        else:
            log(f"[WORKER] skip type={t}")

# ---------------------------------------------------------------------------
# Monologue Thread
# ---------------------------------------------------------------------------
class MonologueThread(threading.Thread):
    def __init__(self, api: RitsuAPI, vmc: VMCClient, tts: TTSEngine):
        super().__init__(daemon=True, name="monologue")
        self.api = api
        self.vmc = vmc
        self.tts = tts

    def run(self):
        enable = _env_bool("RITSU_MONOLOGUE_ENABLE", False)
        idle_need = _env_int("RITSU_MONOLOGUE_IDLE_SEC", 600)
        cooldown = _env_int("RITSU_MONOLOGUE_COOLDOWN_SEC", 900)
        max_day = _env_int("RITSU_MONOLOGUE_MAX_PER_DAY", 20)
        time_range = _env("RITSU_MONOLOGUE_TIME_RANGE", "08:00-23:00")
        tick = _env_int("RITSU_MONOLOGUE_TICK_SEC", 5)
        conv_id = _env("RITSU_MONOLOGUE_CONVERSATION_ID", "monologue")

        count = 0
        last_fire = 0.0
        log(f"[MONO] start enable={enable} idle={idle_need}s cooldown={cooldown}s")

        while True:
            try:
                if not _env_bool("RITSU_MONOLOGUE_ENABLE", False):
                    time.sleep(tick); continue
                idle = get_idle_seconds()
                now_dt = datetime.now()
                if idle < idle_need:
                    time.sleep(tick); continue
                if cooldown > 0 and (time.time() - last_fire) < cooldown:
                    time.sleep(tick); continue
                if max_day >= 0 and count >= max_day:
                    time.sleep(tick); continue
                # Time range check
                try:
                    s, e = time_range.split("-")
                    sh, sm = map(int, s.split(":")); eh, em = map(int, e.split(":"))
                    t = now_dt.time()
                    st, et = dtime(sh, sm), dtime(eh, em)
                    if st <= et:
                        if not (st <= t <= et): time.sleep(tick); continue
                    else:
                        if not (t >= st or t <= et): time.sleep(tick); continue
                except: pass

                title = get_foreground_title()
                prompt = (
                    f"【独り言モード】\n- 現在時刻: {now_dt.strftime('%H:%M')}\n"
                    f"- 無操作: {idle}秒\n- アクティブウィンドウ: {title[:80]}\n\n"
                    "短く1〜2文。作業の邪魔にならないトーンで。"
                )
                data = self.api.send_v2(prompt, conv_id)
                reply = data.get("reply_text", "")
                tag = data.get("emotion_tag", "neutral")
                if reply:
                    notify("律（独り言）", reply)
                    self.tts.speak(reply)
                    self.vmc.send_expression(tag, hold_ms=900, fade_ms=700)
                last_fire = time.time()
                count += 1
                log(f"[MONO] fired #{count}: {reply[:60]}")
                time.sleep(tick)
            except Exception as e:
                log(f"[MONO] error: {e}")
                time.sleep(10)

# ---------------------------------------------------------------------------
# GUI (tkinter)
# ---------------------------------------------------------------------------
class RitsuGUI:
    def __init__(self, api: RitsuAPI, vmc: VMCClient, tts: TTSEngine,
                 stt: STTEngine, ptt: PTTRecorder):
        self.api = api
        self.vmc = vmc
        self.tts = tts
        self.stt = stt
        self.ptt = ptt
        self._ptt_active = False
        self._build()

    def _build(self):
        self.root = tk.Tk()
        self.root.title("律 v3")
        self.root.attributes("-topmost", True)
        self.root.geometry("480x380")
        self.root.configure(bg="#1e1e2e")
        self.root.protocol("WM_DELETE_WINDOW", self._hide)

        # Title bar
        tk.Label(self.root, text="律 — 統合クライアント v3",
                 bg="#1e1e2e", fg="#cdd6f4", font=("Segoe UI", 11, "bold"),
                 anchor="w").pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(self.root, text="Enter=送信 / Esc=隠す / XButton1=表示切替 / XButton2=PTT",
                 bg="#1e1e2e", fg="#6c7086", font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", padx=10)

        # Input
        self.inp = tk.Text(self.root, height=3, wrap="word",
                           bg="#313244", fg="#cdd6f4", insertbackground="#cdd6f4",
                           font=("Segoe UI", 10), relief="flat", padx=8, pady=6)
        self.inp.pack(fill="x", padx=10, pady=(6, 0))
        self.inp.bind("<Return>", self._on_enter)
        self.inp.bind("<Escape>", lambda e: self._hide())

        # Send button
        btn_frame = tk.Frame(self.root, bg="#1e1e2e")
        btn_frame.pack(fill="x", padx=10, pady=4)
        self.send_btn = tk.Button(btn_frame, text="送信", command=self._send,
                                  bg="#89b4fa", fg="#1e1e2e",
                                  font=("Segoe UI", 9, "bold"),
                                  relief="flat", padx=16, pady=2)
        self.send_btn.pack(side="left")
        self.status = tk.Label(btn_frame, text="", bg="#1e1e2e", fg="#a6adc8",
                               font=("Segoe UI", 8))
        self.status.pack(side="right")

        # Output
        self.out = tk.Text(self.root, height=10, wrap="word", state="disabled",
                           bg="#181825", fg="#bac2de", font=("Segoe UI", 10),
                           relief="flat", padx=8, pady=6)
        self.out.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Hotkeys (global via root binding)
        self.root.bind_all("<Escape>", lambda e: self._hide())

        # Start hidden
        self.root.withdraw()
        self._visible = False

    def toggle(self):
        if self._visible:
            self._hide()
        else:
            self._show()

    def _show(self):
        self.root.deiconify()
        self.root.lift()
        self.inp.focus_set()
        self._visible = True

    def _hide(self):
        self.root.withdraw()
        self._visible = False

    def _on_enter(self, event):
        if not event.state & 0x1:  # Not Shift+Enter
            self._send()
            return "break"

    def _send(self):
        text = self.inp.get("1.0", "end").strip()
        if not text:
            return
        self.inp.delete("1.0", "end")
        self.status.config(text="送信中...")
        self._append_out(f"→ {text}\n")
        threading.Thread(target=self._do_send, args=(text,), daemon=True).start()

    def _do_send(self, text: str):
        try:
            t0 = time.time()
            data = self.api.send_text(text)
            elapsed = time.time() - t0
            reply = data.get("reply_text", "(no reply)")
            tag = data.get("emotion_tag", "neutral")
            log(f"[GUI] reply received: tag={tag} len={len(reply)}")
            self.root.after(0, self._on_reply, reply, tag, elapsed)
            # TTS + VMC in background
            self.tts.speak(reply)
            self.vmc.send_expression(tag, hold_ms=900)
        except Exception as e:
            self.root.after(0, self._on_reply, f"ERROR: {e}", "warn", 0)

    def _on_reply(self, reply: str, tag: str, elapsed: float):
        self._append_out(f"律: {reply}\n\n")
        self.status.config(text=f"[{tag}] {elapsed:.1f}s")

    def _append_out(self, text: str):
        self.out.config(state="normal")
        self.out.insert("end", text)
        self.out.see("end")
        self.out.config(state="disabled")

    def ptt_start(self):
        if not self._ptt_active:
            self._ptt_active = True
            self.ptt.start_recording()
            self.status.config(text="🎤 録音中...")

    def ptt_stop(self):
        if self._ptt_active:
            self._ptt_active = False
            wav = self.ptt.stop_recording()
            self.status.config(text="音声認識中...")
            if wav:
                threading.Thread(target=self._ptt_transcribe, args=(wav,), daemon=True).start()

    def _ptt_transcribe(self, wav_path: str):
        try:
            from openai import OpenAI
            key_path = Path.home() / ".ritsu" / "openai_key.txt"
            key = key_path.read_text(encoding="utf-8").strip() if key_path.exists() else ""
            if not key:
                self.root.after(0, self._append_out, "(STT: API key missing)\n")
                return
            client = OpenAI(api_key=key)
            with open(wav_path, "rb") as f:
                t = client.audio.transcriptions.create(model="gpt-4o-mini-transcribe", file=f)
            text = getattr(t, "text", str(t)).strip()
            if text:
                self.root.after(0, self._ptt_send, text)
        except Exception as e:
            self.root.after(0, self._append_out, f"(STT error: {e})\n")

    def _ptt_send(self, text: str):
        self.inp.delete("1.0", "end")
        self.inp.insert("1.0", text)
        self._send()

    def run(self):
        self.root.mainloop()

# ---------------------------------------------------------------------------
# Global Hotkeys
# ---------------------------------------------------------------------------
def setup_hotkeys(gui: RitsuGUI):
    """Setup global hotkeys using 'keyboard' library."""
    try:
        import keyboard
        keyboard.add_hotkey("F10", gui.toggle, suppress=False)
        # XButton1/XButton2 need mouse hook - use pynput for that
        try:
            from pynput import mouse
            def on_click(x, y, button, pressed):
                if button == mouse.Button.x1:
                    if pressed:
                        gui.root.after(0, gui.toggle)
                elif button == mouse.Button.x2:
                    if pressed:
                        gui.root.after(0, gui.ptt_start)
                    else:
                        gui.root.after(0, gui.ptt_stop)
            listener = mouse.Listener(on_click=on_click)
            listener.daemon = True
            listener.start()
            log("[HOTKEY] mouse buttons active (pynput)")
        except ImportError:
            log("[HOTKEY] pynput not installed, mouse buttons disabled")
        log("[HOTKEY] F10=toggle, XButton1=toggle, XButton2=PTT")
    except ImportError:
        log("[HOTKEY] 'keyboard' not installed. pip install keyboard pynput")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="律 統合クライアント v3")
    parser.add_argument("--env", default="", help=".env file path")
    parser.add_argument("--no-tunnel", action="store_true", help="Disable SSH tunnel")
    parser.add_argument("--no-gui", action="store_true", help="Worker-only mode")
    args = parser.parse_args()

    _init_log()
    log("=== 律 v3 starting ===")

    # Load env
    script_dir = Path(__file__).parent
    env_candidates = [
        args.env,
        str(script_dir / ".ritsu_worker.env"),
        str(script_dir / "ritsu_worker.env"),
        str(Path.home() / ".ritsu" / ".ritsu_worker.env"),
    ]
    for p in env_candidates:
        if p and os.path.isfile(p):
            _load_env_file(p)
            log(f"[ENV] loaded: {p}")
            break

    base_url = _env("RITSU_BASE_URL", "http://127.0.0.1:18181")
    token = _env("RITSU_BEARER_TOKEN")
    worker_id = _env("RITSU_WORKER_ID") or socket.gethostname()

    if not token:
        log("[FATAL] RITSU_BEARER_TOKEN is empty")
        sys.exit(2)

    # SSH Tunnel
    tunnel = None
    ssh_host = _env("RITSU_SSH_HOST", "")
    if not args.no_tunnel and ssh_host:
        tunnel = SSHTunnel(
            ssh_host=ssh_host,
            local_port=_env_int("RITSU_SSH_LOCAL_PORT", 18181),
            remote_port=_env_int("RITSU_SSH_REMOTE_PORT", 8181),
            ssh_port=_env_int("RITSU_SSH_PORT", 22),
        )
        tunnel.start()
        time.sleep(2)  # Wait for tunnel

    # API
    api = RitsuAPI(base_url, token, timeout=_env_float("RITSU_HTTP_TIMEOUT_SEC", 15))
    log(f"[API] base={base_url} ready={api.ready()}")

    # VMC
    vmc = VMCClient(
        host=_env("RITSU_VMC_HOST", "127.0.0.1"),
        port=_env_int("RITSU_VMC_PORT", 39539),
        expr_map_path=_env("RITSU_VMC_MAP_PATH",
                           str(script_dir / "vmc_expr_map.json")),
    )

    # TTS
    tts = TTSEngine(
        base_url=_env("VOICEVOX_URL", "http://127.0.0.1:50021"),
        speaker_name=_env("RITSU_VOICEVOX_SPEAKER_NAME", "四国めたん"),
    )

    # STT + PTT
    stt = STTEngine()
    ptt = PTTRecorder()

    # Worker thread (action polling)
    worker = WorkerThread(api, vmc, tts, worker_id,
                          poll_sec=_env_float("RITSU_POLL_SEC", 1.0))
    worker.start()

    # Monologue thread
    mono = MonologueThread(api, vmc, tts)
    mono.start()

    log(f"[BOOT] worker_id={worker_id}")
    log(f"[BOOT] vmc={vmc.host}:{vmc.port}")

    if args.no_gui:
        log("[MODE] worker-only (no GUI)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log("Ctrl+C → exit")
    else:
        # GUI
        gui = RitsuGUI(api, vmc, tts, stt, ptt)
        setup_hotkeys(gui)
        gui._show()
        log("[BOOT] GUI ready")
        gui.run()

    if tunnel:
        tunnel.stop()
    log("=== 律 v3 exit ===")

if __name__ == "__main__":
    main()
