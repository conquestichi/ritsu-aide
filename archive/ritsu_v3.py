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
  [Monologue Thread]→ Idle detect + Schedule → /assistant/v2 or fixed text
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
# Single Instance Guard
# ---------------------------------------------------------------------------
_instance_socket: Optional[socket.socket] = None

def _acquire_single_instance_lock() -> bool:
    """
    Try to bind to 127.0.0.1:59181 to ensure single instance.
    Returns True if lock acquired, False if another instance is running.
    """
    global _instance_socket
    try:
        _instance_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _instance_socket.bind(("127.0.0.1", 59181))
        _instance_socket.listen(1)
        return True
    except OSError:
        # Port already bound - another instance is running
        return False

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
                 remote_port: int = 8181, ssh_port: int = 22,
                 ssh_key_path: str = ""):
        self.ssh_host = ssh_host
        self.local_port = local_port
        self.remote_port = remote_port
        self.ssh_port = ssh_port
        self.ssh_key_path = ssh_key_path
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
        ]
        if self.ssh_key_path and os.path.exists(self.ssh_key_path):
            args.extend(["-i", self.ssh_key_path])
        args.append(self.ssh_host)
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
        self._active_expr: Optional[tuple[str, float]] = None  # (blendshape, value)
        self._expr_fade_cancel = threading.Event()
        self._speaking_event: Optional[threading.Event] = None  # ref to tts.speaking
        self._load_map(expr_map_path)

    def _ensure_client(self):
        if self._client is None:
            try:
                from pythonosc.udp_client import SimpleUDPClient
                self._client = SimpleUDPClient(self.host, self.port)
                self._client.send_message("/VMC/Ext/OK", [1])
                log(f"[VMC] connected {self.host}:{self.port}")
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

    # ----- send_expression ------------------------------------------------

    def send_expression(self, tag: str, value: float = 1.0,
                        hold_ms: int = 2000, fade_ms: int = 400) -> None:
        """Register expression BlendShape.

        If TTS is currently playing, only stores the value in _active_expr
        (clamped to min 0.8) so the lipsync thread can bundle it with
        mouth-A in one Apply.
        If TTS is NOT playing, sends Val+Apply immediately (value=1.0)
        and schedules a hold→fade cycle (hold_ms=2000ms).
        """
        bs = self.expr_map.get(tag, "")
        speaking = bool(self._speaking_event and self._speaking_event.is_set())

        if not bs:
            log(f"[VMC] expression: tag={tag} bs=UNKNOWN val={value} speaking={speaking} -> SKIPPED (no mapping)")
            return

        # Cancel any pending standalone fade
        self._expr_fade_cancel.set()

        # If TTS is active, lipsync thread will include this in its Apply
        if speaking:
            # Clamp expression value to min 0.8 during TTS for visibility
            tts_value = max(float(value), 0.8)
            self._active_expr = (bs, tts_value)
            log(f"[VMC] expression: tag={tag} bs={bs} val={value}->{tts_value} speaking=True hold=deferred (lipsync will send)")
            return

        # Standalone: ensure full expression strength
        standalone_value = max(float(value), 1.0)
        self._active_expr = (bs, standalone_value)
        log(f"[VMC] expression: tag={tag} bs={bs} val={value}->{standalone_value} speaking=False hold={hold_ms}ms fade={fade_ms}ms (standalone)")

        cancel = threading.Event()
        self._expr_fade_cancel = cancel
        threading.Thread(target=self._standalone_expr,
                         args=(bs, standalone_value, hold_ms, fade_ms, cancel),
                         daemon=True, name="vmc-expr").start()

    def _standalone_expr(self, bs: str, value: float,
                         hold_ms: int, fade_ms: int,
                         cancel: threading.Event) -> None:
        """Standalone expression: send immediately, hold, then fade."""
        c = self._ensure_client()
        if not c:
            return
        try:
            with self._lock:
                c.send_message("/VMC/Ext/OK", [1])
                c.send_message("/VMC/Ext/Blend/Val", [bs, float(value)])
                c.send_message("/VMC/Ext/Blend/Apply", [])
            log(f"[VMC] standalone apply: bs={bs} val={value} hold={hold_ms}ms fade={fade_ms}ms")

            # Hold
            if cancel.wait(hold_ms / 1000.0):
                log(f"[VMC] standalone cancelled during hold: bs={bs}")
                return

            # Fade
            if fade_ms > 0:
                steps = max(int(fade_ms / 33), 1)
                for i in range(steps):
                    if cancel.is_set():
                        return
                    frac = 1.0 - (i + 1) / steps
                    with self._lock:
                        c.send_message("/VMC/Ext/OK", [1])
                        c.send_message("/VMC/Ext/Blend/Val",
                                       [bs, float(value * frac)])
                        c.send_message("/VMC/Ext/Blend/Apply", [])
                    time.sleep(0.033)

            # Final reset
            with self._lock:
                c.send_message("/VMC/Ext/OK", [1])
                c.send_message("/VMC/Ext/Blend/Val", [bs, 0.0])
                c.send_message("/VMC/Ext/Blend/Apply", [])

            # Clear if still ours
            if self._active_expr and self._active_expr[0] == bs:
                self._active_expr = None
                log(f"[VMC] standalone fade done, cleared {bs}")
        except Exception as e:
            log(f"[VMC] standalone expr error: {e}")

    # ----- lip sync (called ~30 fps from TTS thread) ----------------------

    def send_lipsync(self, a_value: float) -> None:
        """Send mouth-A + active expression BlendShape in one Apply."""
        c = self._ensure_client()
        if not c:
            log("[LIPSYNC] send_lipsync: no client!")
            return
        try:
            with self._lock:
                c.send_message("/VMC/Ext/OK", [1])
                c.send_message("/VMC/Ext/Blend/Val", ["A", float(a_value)])
                expr = self._active_expr
                if expr:
                    c.send_message("/VMC/Ext/Blend/Val",
                                   [expr[0], float(expr[1])])
                c.send_message("/VMC/Ext/Blend/Apply", [])
        except Exception as exc:
            log(f"[LIPSYNC] send_lipsync error: {exc}")

    def reset_lipsync(self) -> None:
        """Close mouth (A=0), preserving active expression."""
        c = self._ensure_client()
        if not c:
            return
        try:
            with self._lock:
                c.send_message("/VMC/Ext/OK", [1])
                c.send_message("/VMC/Ext/Blend/Val", ["A", 0.0])
                expr = self._active_expr
                if expr:
                    c.send_message("/VMC/Ext/Blend/Val",
                                   [expr[0], float(expr[1])])
                c.send_message("/VMC/Ext/Blend/Apply", [])
        except Exception:
            pass

# ---------------------------------------------------------------------------
# TTS (VOICEVOX)
# ---------------------------------------------------------------------------
class TTSEngine:
    PRESETS = {
        "amaama": {"speed": 0.98, "pitch": 0.00, "intonation": 1.20, "volume": 1.00, "style_id": 0},
        "sexy":   {"speed": 1.03, "pitch": 0.00, "intonation": 1.02, "volume": 0.95, "style_id": 4},
    }
    SEXY_WORDS = ("\u306d\u3048", "\u3060\u3088", "\u304b\u306a", "\u304a\u9858\u3044",
                  "\u3060\u3081", "\u3059\u304d", "\u597d\u304d", "\u304a\u306f\u3088",
                  "\u304a\u3084\u3059\u307f")  # ねえ,だよ,かな,お願い,だめ,すき,好き,おはよ,おやすみ

    def __init__(self, base_url: str = "http://127.0.0.1:50021",
                 speaker_name: str = "",
                 default_style_id: int = 0):
        self.base = base_url
        self.default_style_id = default_style_id
        self.speaking = threading.Event()  # set while audio is playing
        self._vmc: Optional[Any] = None  # VMCClient ref for lip sync
        self._queue: queue.Queue = queue.Queue()
        self._cable_idx, self._speaker_idx = self._discover_output_devices()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="tts")
        self._thread.start()

    def _discover_output_devices(self) -> tuple[Optional[int], Optional[int]]:
        """Discover CABLE Input and speaker device indices.

        Returns (cable_idx, speaker_idx).  Both use MME (hostapi 0) because
        MME's OutputStream.write() blocks until playback completes, which
        keeps chunk sequencing correct.
        """
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            cable_idx: Optional[int] = None
            speaker_idx: Optional[int] = None

            # Find CABLE Input (MME)
            for i, d in enumerate(devices):
                if ("cable input" in d["name"].lower()
                        and d["max_output_channels"] > 0
                        and d.get("hostapi", -1) == 0):
                    cable_idx = i
                    log(f"[TTS] Found CABLE Input: [{i}] {d['name']}")
                    break

            # Speaker: default output if it is NOT a CABLE device
            try:
                def_out = int(sd.default.device[1])
                if def_out >= 0:
                    d = devices[def_out]
                    if ("cable" not in d["name"].lower()
                            and d["max_output_channels"] > 0):
                        speaker_idx = def_out
                        log(f"[TTS] Speaker (default): [{def_out}] {d['name']}")
            except Exception:
                pass

            # If default IS cable, find first non-CABLE MME output
            if speaker_idx is None:
                for i, d in enumerate(devices):
                    if (d["max_output_channels"] > 0
                            and d.get("hostapi", -1) == 0
                            and "cable" not in d["name"].lower()
                            and "vb-audio" not in d["name"].lower()
                            and i != cable_idx):
                        speaker_idx = i
                        log(f"[TTS] Speaker (fallback): [{i}] {d['name']}")
                        break

            if cable_idx is None:
                log("[TTS] CABLE Input not found")
            if speaker_idx is None:
                log("[TTS] Speaker not found, will use winsound fallback")
            return cable_idx, speaker_idx
        except Exception as e:
            log(f"[TTS] Device discovery failed: {e}")
            return None, None

    def _play_wav(self, wav_bytes: bytes) -> None:
        """Play WAV on speaker + CABLE Input, with VMC lip sync.

        Both audio devices are addressed by explicit index.
        A parallel lip-sync thread analyses RMS volume per 33 ms frame
        and sends mouth-A BlendShape to VMagicMirror via VMC protocol.
        """
        targets = [i for i in (self._cable_idx, self._speaker_idx)
                    if i is not None]

        if not targets:
            tmp = Path(os.environ.get("TEMP", ".")) / "ritsu_tts_out.wav"
            tmp.write_bytes(wav_bytes)
            import winsound
            log("[TTS] _play_wav: winsound fallback")
            winsound.PlaySound(str(tmp), winsound.SND_FILENAME)
            return

        import io, wave
        import numpy as np
        import sounddevice as sd

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            rate = wf.getframerate()
            ch = wf.getnchannels()
            raw = wf.readframes(wf.getnframes())
        audio_f = (np.frombuffer(raw, dtype="int16")
                     .reshape(-1, ch)
                     .astype(np.float32) / 32768.0)
        duration = len(audio_f) / rate
        log(f"[TTS] _play_wav: {rate}Hz {ch}ch {duration:.1f}s → {targets}")

        # --- Pre-compute lip sync RMS per frame (~30 fps) ----------------
        lip_values: list[float] = []
        vmc = self._vmc
        if vmc is not None:
            frame_ms = 33          # ~30 fps
            frame_sz = max(int(rate * frame_ms / 1000), 1)
            # mono mix for RMS
            mono = audio_f.mean(axis=1) if ch > 1 else audio_f[:, 0]
            rms_list: list[float] = []
            for i in range(0, len(mono), frame_sz):
                chunk = mono[i:i + frame_sz]
                rms_list.append(float(np.sqrt(np.mean(chunk ** 2))))
            rms_peak = max(rms_list) if rms_list else 0.01
            threshold = 0.015
            log(f"[LIPSYNC] pre-compute: {len(rms_list)} frames, "
                f"frame_sz={frame_sz}, rms_peak={rms_peak:.4f}, thr={threshold}")
            # Map RMS → mouth A (0.0-1.0) with smoothing
            prev = 0.0
            smooth_up = 0.55   # fast open
            smooth_down = 0.35 # slower close (natural)
            for rms in rms_list:
                if rms < threshold:
                    target = 0.0
                else:
                    target = min((rms / rms_peak) * 1.2, 1.0)
                alpha = smooth_up if target > prev else smooth_down
                val = prev + alpha * (target - prev)
                lip_values.append(round(val, 3))
                prev = val
            nonzero = sum(1 for v in lip_values if v > 0.01)
            log(f"[LIPSYNC] lip_values: {len(lip_values)} total, "
                f"{nonzero} nonzero, "
                f"first5={lip_values[:5]}, last5={lip_values[-5:]}")
        else:
            log("[LIPSYNC] vmc is None — lip sync disabled")

        # --- Lip sync sender thread --------------------------------------
        def _lipsync_sender() -> None:
            frame_interval = 0.033
            total = len(lip_values)
            log(f"[LIPSYNC] sender START: {total} frames, interval={frame_interval}s")
            t0 = time.time()
            sent = 0
            try:
                for idx, val in enumerate(lip_values):
                    target_time = t0 + idx * frame_interval
                    wait = target_time - time.time()
                    if wait > 0:
                        time.sleep(wait)
                    vmc.send_lipsync(val)
                    sent += 1
                    if idx < 5 or idx % 10 == 0 or idx == total - 1:
                        log(f"[LIPSYNC] frame {idx}/{total} A={val:.3f} "
                            f"t={time.time()-t0:.2f}s")
            except Exception as exc:
                log(f"[LIPSYNC] sender ERROR at frame {sent}: {exc}")
            elapsed = time.time() - t0
            log(f"[LIPSYNC] sender END: sent {sent}/{total} in {elapsed:.2f}s")
            vmc.reset_lipsync()

        # --- Audio playback per device ------------------------------------
        def _play_single(dev_idx: int) -> None:
            try:
                dev_name = sd.query_devices(dev_idx)["name"]
                log(f"[TTS] play start: [{dev_idx}] {dev_name}")
                t0 = time.time()
                with sd.OutputStream(samplerate=rate, channels=ch,
                                     dtype="float32", device=dev_idx) as stream:
                    stream.write(audio_f)
                elapsed = time.time() - t0
                remaining = duration - elapsed
                if remaining > 0.05:
                    time.sleep(remaining)
                log(f"[TTS] play done:  [{dev_idx}] ({time.time()-t0:.1f}s)")
            except Exception as exc:
                log(f"[TTS] play error: [{dev_idx}] {exc}")

        # --- Launch all threads simultaneously ----------------------------
        threads: list[threading.Thread] = []
        if lip_values:
            threads.append(threading.Thread(target=_lipsync_sender,
                                            daemon=True, name="lipsync"))
        for d in targets:
            threads.append(threading.Thread(target=_play_single, args=(d,),
                                            daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def speak(self, text: str) -> None:
        if text.strip():
            self._queue.put(text.strip())

    def _worker(self) -> None:
        import urllib.request, urllib.parse
        log("[TTS] worker thread started")
        while True:
            text = self._queue.get()
            try:
                self.speaking.set()
                chunks = self._split(text)
                for chunk, pause in chunks:
                    style = "sexy" if self._is_sexy(chunk) else "amaama"
                    preset = self.PRESETS[style]
                    sid = preset["style_id"]
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
                    # play on configured output devices
                    self._play_wav(wav)
                    time.sleep(pause)
            except Exception as e:
                log(f"[TTS] error: {e}")
            finally:
                self.speaking.clear()

    def _is_sexy(self, text: str) -> bool:
        # Disabled by default. Set RITSU_TTS_SEXY=1 to enable.
        if not _env_bool("RITSU_TTS_SEXY", False):
            return False
        t = text.strip()
        if not t or len(t) > 15: return False
        if any(w in t for w in self.SEXY_WORDS):
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
        # Priority: RITSU_STT_API_KEY > OPENAI_API_KEY > ~/.ritsu/openai_key.txt
        self._key = os.environ.get("RITSU_STT_API_KEY", "").strip()
        if not self._key:
            self._key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not self._key:
            kp = Path.home() / ".ritsu" / "openai_key.txt"
            if kp.exists():
                self._key = kp.read_text(encoding="utf-8").strip()
        if self._key:
            log(f"[STT] API key loaded ({len(self._key)} chars)")
        else:
            log("[STT] WARNING: No API key found")

    def record_and_transcribe(self, seconds: float = 5.0) -> str:
        if not self._key:
            return "(STT: API key not found)"
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
        # VPS returns flat actions (e.g. {"type":"emotion","tag":"happy",...})
        # with no nested "payload" key — fall back to item itself.
        payload = item.get("payload") or item
        if not isinstance(payload, dict):
            payload = {}
        if t == "notify":
            text = payload.get("text", str(payload))
            notify("律", str(text))
        elif t in ("emotion", "vmc_expression"):
            tag = payload.get("tag") or payload.get("emotion_tag", "neutral")
            val = float(payload.get("value", 1.0))
            log(f"[WORKER] dispatch emotion: type={t} tag={tag} val={val} payload={payload}")
            self.vmc.send_expression(tag, value=val)
            log(f"[WORKER] send_expression returned: tag={tag} val={val}")
        elif t == "speak":
            text = payload.get("text") or payload.get("reply_text") or payload.get("message", "")
            if text:
                self.tts.speak(str(text))
        elif t == "gesture":
            log(f"[WORKER] gesture: {payload.get('name', '?')}")
        else:
            log(f"[WORKER] skip type={t}")

# ---------------------------------------------------------------------------
# Monologue Thread (idle検知 + スケジュール定時発話 統合)
# ---------------------------------------------------------------------------
class MonologueThread(threading.Thread):
    def __init__(self, api: RitsuAPI, vmc: VMCClient, tts: TTSEngine):
        super().__init__(daemon=True, name="monologue")
        self.api = api
        self.vmc = vmc
        self.tts = tts

    # --- Schedule loader ---
    @staticmethod
    def _load_schedule(path: str) -> list[dict]:
        """monologue_schedule.json を読み込み、slotsリストを返す"""
        try:
            p = Path(path)
            if not p.exists():
                log(f"[MONO-SCH] schedule file not found: {path}")
                return []
            data = json.loads(p.read_text(encoding="utf-8"))
            slots = data.get("slots", [])
            log(f"[MONO-SCH] loaded {len(slots)} slots from {path}")
            return slots
        except Exception as e:
            log(f"[MONO-SCH] load error: {e}")
            return []

    # --- Time range check ---
    @staticmethod
    def _in_time_range(now_t, time_range: str) -> bool:
        try:
            s, e = time_range.split("-")
            sh, sm = map(int, s.split(":")); eh, em = map(int, e.split(":"))
            st, et = dtime(sh, sm), dtime(eh, em)
            if st <= et:
                return st <= now_t <= et
            else:
                return now_t >= st or now_t <= et
        except:
            return True  # パース失敗時は制限なし

    def run(self):
        # --- Idle mode config ---
        idle_need = _env_int("RITSU_MONOLOGUE_IDLE_SEC", 600)
        cooldown = _env_int("RITSU_MONOLOGUE_COOLDOWN_SEC", 900)
        max_day = _env_int("RITSU_MONOLOGUE_MAX_PER_DAY", 20)
        time_range = _env("RITSU_MONOLOGUE_TIME_RANGE", "08:00-23:00")
        tick = _env_int("RITSU_MONOLOGUE_TICK_SEC", 5)
        conv_id = _env("RITSU_MONOLOGUE_CONVERSATION_ID", "monologue")

        # --- Schedule config ---
        schedule_enable = _env_bool("RITSU_MONOLOGUE_SCHEDULE_ENABLE", False)
        schedule_path = _env("RITSU_MONOLOGUE_SCHEDULE_PATH", "monologue_schedule.json")
        schedule_tolerance = _env_int("RITSU_MONOLOGUE_SCHEDULE_TOLERANCE_SEC", 120)
        slots = self._load_schedule(schedule_path) if schedule_enable else []

        # --- State ---
        idle_count = 0
        idle_last_fire = 0.0
        fired_slots: set[str] = set()  # "HH:MM" 当日発火済み
        last_date = datetime.now().date()

        log(f"[MONO] start idle_enable={_env_bool('RITSU_MONOLOGUE_ENABLE', False)} "
            f"idle={idle_need}s cooldown={cooldown}s "
            f"schedule_enable={schedule_enable} slots={len(slots)}")

        while True:
            try:
                now_dt = datetime.now()
                now_t = now_dt.time()

                # 日付変更 → スケジュール発火履歴リセット
                if now_dt.date() != last_date:
                    fired_slots.clear()
                    idle_count = 0
                    last_date = now_dt.date()
                    log("[MONO] new day -> reset counters")

                # ======================
                # Schedule型 (定時発話)
                # ======================
                if schedule_enable and _env_bool("RITSU_MONOLOGUE_SCHEDULE_ENABLE", False):
                    for slot in slots:
                        at_str = slot.get("at", "")
                        if at_str in fired_slots:
                            continue
                        try:
                            sh, sm = map(int, at_str.split(":"))
                            # 現在時刻がスケジュール時刻から tolerance 秒以内か
                            sched_secs = sh * 3600 + sm * 60
                            now_secs = now_dt.hour * 3600 + now_dt.minute * 60 + now_dt.second
                            diff = now_secs - sched_secs
                            if 0 <= diff <= schedule_tolerance:
                                text = slot.get("text", "")
                                tag = slot.get("emotion_tag", "neutral")
                                if text:
                                    log(f"[MONO-SCH] firing slot {at_str}: {text[:40]}")
                                    notify("律（定時）", text)
                                    self.tts.speak(text)
                                    self.vmc.send_expression(tag, hold_ms=900, fade_ms=700)
                                fired_slots.add(at_str)
                        except Exception as e:
                            log(f"[MONO-SCH] slot parse error {at_str}: {e}")

                # ======================
                # Idle型 (無操作検知)
                # ======================
                if _env_bool("RITSU_MONOLOGUE_ENABLE", False):
                    idle = get_idle_seconds()
                    if (idle >= idle_need
                            and (cooldown <= 0 or (time.time() - idle_last_fire) >= cooldown)
                            and (max_day < 0 or idle_count < max_day)
                            and self._in_time_range(now_t, time_range)):
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
                        idle_last_fire = time.time()
                        idle_count += 1
                        log(f"[MONO] idle fired #{idle_count}: {reply[:60]}")

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
            data = self.api.send_v2(text, conv_id="gui")
            elapsed = time.time() - t0
            reply = data.get("reply_text", "(no reply)")
            tag = data.get("emotion_tag", "neutral")
            should_speak = data.get("should_speak", True)
            log(f"[GUI] reply received: tag={tag} len={len(reply)} should_speak={should_speak}")
            self.root.after(0, self._on_reply, reply, tag, elapsed)
            # TTS — this is the SOLE TTS call for GUI interactions.
            # VMC expression is handled by WorkerThread via enqueued emotion action.
            if should_speak and reply.strip():
                self.tts.speak(reply)
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
            if wav:
                self.status.config(text="音声認識中...")
                threading.Thread(target=self._ptt_transcribe, args=(wav,), daemon=True).start()
            else:
                self.status.config(text="録音失敗")

    def _ptt_transcribe(self, wav_path: str):
        try:
            from openai import OpenAI
            key = self.stt._key
            if not key:
                self.root.after(0, self._append_out, "(STT: API key missing)\n")
                self.root.after(0, lambda: self.status.config(text="API key missing"))
                return
            client = OpenAI(api_key=key)
            with open(wav_path, "rb") as f:
                t = client.audio.transcriptions.create(model="gpt-4o-mini-transcribe", file=f)
            text = getattr(t, "text", str(t)).strip()
            if text:
                self.root.after(0, self._ptt_send, text)
            else:
                self.root.after(0, lambda: self.status.config(text="認識失敗（無音？）"))
        except Exception as e:
            log(f"[STT] transcription error: {e}")
            self.root.after(0, self._append_out, f"(STT error: {e})\n")
            self.root.after(0, lambda: self.status.config(text=f"STT error: {str(e)[:30]}"))

    def _ptt_send(self, text: str):
        self.inp.delete("1.0", "end")
        self.inp.insert("1.0", text)
        self._send()

    def run(self):
        self.root.mainloop()

# ---------------------------------------------------------------------------
# Global Hotkeys (Windows API mouse hook)
# ---------------------------------------------------------------------------
# All hook-related definitions at module level (matching simple_mouse_test.py)
_mouse_hook_proc = None  # Keep reference to prevent garbage collection
_mouse_hook_id = None
_hotkey_gui: Optional[RitsuGUI] = None  # GUI reference for callback

_hk_user32 = ctypes.windll.user32

# Configure argtypes/restype for 64-bit correctness (identical to simple_mouse_test.py)
_hk_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
_hk_user32.CallNextHookEx.restype = ctypes.wintypes.LPARAM
_hk_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD]
_hk_user32.SetWindowsHookExW.restype = ctypes.c_void_p

# Hook constants
_WH_MOUSE_LL = 14
_WM_XBUTTONDOWN = 0x020B
_WM_XBUTTONUP = 0x020C
_WM_HOTKEY = 0x0312
_VK_F10 = 0x79
_HOTKEY_ID_F10 = 1

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", _POINT),
        ("mouseData", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
    ]

def _mouse_hook_callback(nCode, wParam, lParam):
    if nCode >= 0:
        if wParam == _WM_XBUTTONDOWN or wParam == _WM_XBUTTONUP:
            try:
                event = ctypes.cast(lParam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                xbutton = (event.mouseData >> 16) & 0xFFFF
                is_pressed = (wParam == _WM_XBUTTONDOWN)
                gui = _hotkey_gui
                if gui:
                    if xbutton == 1:  # XButton1 (Back button)
                        if is_pressed:
                            gui.root.after(0, gui.toggle)
                    elif xbutton == 2:  # XButton2 (Forward button)
                        if is_pressed:
                            gui.root.after(0, gui.ptt_start)
                        else:
                            gui.root.after(0, gui.ptt_stop)
            except Exception as e:
                log(f"[HOTKEY] Handler error: {e}")

    return _hk_user32.CallNextHookEx(None, nCode, wParam, lParam)

_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.LPARAM, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)


def setup_hotkeys(gui: RitsuGUI):
    """Setup global hotkeys using pure Win32 API (no keyboard library).

    A single dedicated thread handles both:
      - WH_MOUSE_LL hook (XButton1/XButton2)
      - RegisterHotKey (F10)
    All registered in the same thread, served by one GetMessageW loop.
    """
    global _hotkey_gui
    _hotkey_gui = gui

    def _hook_thread():
        global _mouse_hook_proc, _mouse_hook_id
        try:
            # --- Mouse hook ---
            _mouse_hook_proc = _HOOKPROC(_mouse_hook_callback)
            _mouse_hook_id = _hk_user32.SetWindowsHookExW(
                _WH_MOUSE_LL, _mouse_hook_proc, None, 0)
            if _mouse_hook_id:
                log("[HOTKEY] Mouse hook installed (XButton1=toggle, XButton2=PTT)")
            else:
                log("[HOTKEY] Failed to install mouse hook")

            # --- F10 hotkey via RegisterHotKey ---
            if _hk_user32.RegisterHotKey(None, _HOTKEY_ID_F10, 0, _VK_F10):
                log("[HOTKEY] F10=toggle registered (RegisterHotKey)")
            else:
                log("[HOTKEY] Failed to register F10 hotkey")

            # --- Single message loop for both hook and hotkey ---
            msg = ctypes.wintypes.MSG()
            while _hk_user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == _WM_HOTKEY and msg.wParam == _HOTKEY_ID_F10:
                    g = _hotkey_gui
                    if g:
                        g.root.after(0, g.toggle)
                _hk_user32.TranslateMessage(ctypes.byref(msg))
                _hk_user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            log(f"[HOTKEY] Hook thread error: {e}")

    threading.Thread(target=_hook_thread, daemon=True, name="hotkey").start()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Check for single instance
    if not _acquire_single_instance_lock():
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                "律 v3 は既に起動しています。\n複数起動はできません。",
                "律 - 多重起動エラー",
                0x10  # MB_ICONERROR
            )
        except Exception:
            pass
        sys.exit(0)

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
            ssh_key_path=_env("RITSU_SSH_KEY_PATH", ""),
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

    # TTS (style IDs: 四国めたん あまあま=0, セクシー=4)
    tts = TTSEngine(
        base_url=_env("VOICEVOX_URL", "http://127.0.0.1:50021"),
        default_style_id=_env_int("RITSU_VOICEVOX_STYLE_ID", 0),
    )
    tts._vmc = vmc  # VMC lip sync from TTS
    vmc._speaking_event = tts.speaking  # expression defers to lipsync when TTS active

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
