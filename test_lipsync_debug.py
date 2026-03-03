#!/usr/bin/env python3
"""Standalone lip sync debug test.

Synthesizes a phrase with VOICEVOX, computes lip_values with the same
algorithm as ritsu.py, sends to VMagicMirror, and logs every frame.
"""
import io
import json
import math
import time
import urllib.request
import urllib.parse
import wave
import threading

import numpy as np
from pythonosc.udp_client import SimpleUDPClient

VOICEVOX_URL = "http://127.0.0.1:50021"
VMC_HOST = "127.0.0.1"
VMC_PORT = 39539
TEXT = "こんにちは、律です。今日もよろしくお願いします。"
SPEAKER_ID = 0

# --- Synthesize ---
print(f"[1] VOICEVOX synth: {TEXT!r}")
q = urllib.parse.urlencode({"text": TEXT, "speaker": SPEAKER_ID})
req = urllib.request.Request(f"{VOICEVOX_URL}/audio_query?{q}", data=b"", method="POST")
aq = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
body = json.dumps(aq, ensure_ascii=False).encode("utf-8")
req2 = urllib.request.Request(
    f"{VOICEVOX_URL}/synthesis?speaker={SPEAKER_ID}",
    data=body, headers={"Content-Type": "application/json"}, method="POST")
wav_bytes = urllib.request.urlopen(req2, timeout=60).read()

with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
    rate = wf.getframerate()
    ch = wf.getnchannels()
    raw = wf.readframes(wf.getnframes())
audio_f = np.frombuffer(raw, dtype="int16").reshape(-1, ch).astype(np.float32) / 32768.0
duration = len(audio_f) / rate
print(f"    {rate}Hz {ch}ch {duration:.2f}s ({len(audio_f)} samples)")

# --- Pre-compute lip_values (same as ritsu.py) ---
print(f"\n[2] Computing lip_values ...")
frame_ms = 33
frame_sz = max(int(rate * frame_ms / 1000), 1)
mono = audio_f.mean(axis=1) if ch > 1 else audio_f[:, 0]
rms_list = []
for i in range(0, len(mono), frame_sz):
    chunk = mono[i:i + frame_sz]
    rms_list.append(float(np.sqrt(np.mean(chunk ** 2))))

rms_peak = max(rms_list) if rms_list else 0.01
threshold = 0.015
print(f"    frames={len(rms_list)}, frame_sz={frame_sz}, "
      f"rms_peak={rms_peak:.4f}, threshold={threshold}")

prev = 0.0
smooth_up = 0.55
smooth_down = 0.35
lip_values = []
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
print(f"    lip_values: {len(lip_values)} total, {nonzero} nonzero")
print(f"    first 10: {lip_values[:10]}")
print(f"    last 10:  {lip_values[-10:]}")

# Show histogram
print(f"\n    Distribution:")
for lo in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
    hi = lo + 0.1
    cnt = sum(1 for v in lip_values if lo <= v < hi)
    bar = "#" * cnt
    print(f"      {lo:.1f}-{hi:.1f}: {cnt:3d} {bar}")

# --- Send lip sync to VMagicMirror ---
print(f"\n[3] Sending {len(lip_values)} frames to VMC {VMC_HOST}:{VMC_PORT} ...")
client = SimpleUDPClient(VMC_HOST, VMC_PORT)
client.send_message("/VMC/Ext/OK", [1])

frame_interval = 0.033
t0 = time.time()
sent = 0
errors = 0
for idx, val in enumerate(lip_values):
    target_time = t0 + idx * frame_interval
    wait = target_time - time.time()
    if wait > 0:
        time.sleep(wait)
    try:
        client.send_message("/VMC/Ext/Blend/Val", ["A", float(val)])
        client.send_message("/VMC/Ext/Blend/Apply", [])
        sent += 1
    except Exception as exc:
        errors += 1
        if errors <= 3:
            print(f"    ERROR frame {idx}: {exc}")

    # Log every frame
    elapsed = time.time() - t0
    bar = "#" * int(val * 40)
    print(f"    [{idx:3d}/{len(lip_values)}] A={val:.3f} t={elapsed:.2f}s |{bar}")

elapsed = time.time() - t0
print(f"\n    Sent {sent}/{len(lip_values)} frames in {elapsed:.2f}s "
      f"(errors={errors})")

# Reset
client.send_message("/VMC/Ext/Blend/Val", ["A", 0.0])
client.send_message("/VMC/Ext/Blend/Apply", [])
print(f"    Mouth reset to closed.")
print(f"\n[DONE]")
