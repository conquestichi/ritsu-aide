#!/usr/bin/env python3
"""Concurrent audio + lip sync test — mimics ritsu.py _play_wav exactly.

Plays audio on speaker + CABLE while sending lip sync OSC.
This tests whether audio threads interfere with lip sync timing.
"""
import io
import json
import time
import threading
import urllib.request
import urllib.parse
import wave

import numpy as np
import sounddevice as sd
from pythonosc.udp_client import SimpleUDPClient

VOICEVOX_URL = "http://127.0.0.1:50021"
VMC_HOST = "127.0.0.1"
VMC_PORT = 39539
TEXT = "こんにちは、律です。今日もよろしくお願いします。"
SPEAKER_ID = 0

def find_devices():
    devices = sd.query_devices()
    cable_idx = speaker_idx = None
    for i, d in enumerate(devices):
        if ("cable input" in d["name"].lower()
                and d["max_output_channels"] > 0
                and d.get("hostapi", -1) == 0):
            cable_idx = i
            break
    try:
        def_out = int(sd.default.device[1])
        if def_out >= 0:
            d = devices[def_out]
            if ("cable" not in d["name"].lower()
                    and d["max_output_channels"] > 0):
                speaker_idx = def_out
    except Exception:
        pass
    if speaker_idx is None:
        for i, d in enumerate(devices):
            if (d["max_output_channels"] > 0
                    and d.get("hostapi", -1) == 0
                    and "cable" not in d["name"].lower()
                    and "vb-audio" not in d["name"].lower()
                    and i != cable_idx):
                speaker_idx = i
                break
    return cable_idx, speaker_idx

# --- Synthesize ---
print("[1] VOICEVOX synth ...")
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
print(f"    {rate}Hz {ch}ch {duration:.2f}s")

# --- Devices ---
cable_idx, speaker_idx = find_devices()
targets = [i for i in (cable_idx, speaker_idx) if i is not None]
for label, idx in [("CABLE", cable_idx), ("Speaker", speaker_idx)]:
    if idx is not None:
        print(f"    {label}: [{idx}] {sd.query_devices(idx)['name']}")
    else:
        print(f"    {label}: NOT FOUND")

# --- Pre-compute lip sync (same as ritsu.py) ---
print("\n[2] Computing lip_values ...")
frame_ms = 33
frame_sz = max(int(rate * frame_ms / 1000), 1)
mono = audio_f.mean(axis=1) if ch > 1 else audio_f[:, 0]
rms_list = []
for i in range(0, len(mono), frame_sz):
    chunk = mono[i:i + frame_sz]
    rms_list.append(float(np.sqrt(np.mean(chunk ** 2))))
rms_peak = max(rms_list) if rms_list else 0.01
threshold = 0.015
prev = 0.0
lip_values = []
for rms in rms_list:
    if rms < threshold:
        target = 0.0
    else:
        target = min((rms / rms_peak) * 1.2, 1.0)
    alpha = 0.55 if target > prev else 0.35
    val = prev + alpha * (target - prev)
    lip_values.append(round(val, 3))
    prev = val
nonzero = sum(1 for v in lip_values if v > 0.01)
print(f"    {len(lip_values)} frames, {nonzero} nonzero")

# --- VMC client (with lock, exactly like ritsu.py VMCClient) ---
vmc_client = SimpleUDPClient(VMC_HOST, VMC_PORT)
vmc_lock = threading.Lock()
vmc_client.send_message("/VMC/Ext/OK", [1])

lipsync_sent = 0
lipsync_errors = 0

def send_lipsync(a_value: float):
    global lipsync_sent, lipsync_errors
    try:
        with vmc_lock:
            vmc_client.send_message("/VMC/Ext/OK", [1])
            vmc_client.send_message("/VMC/Ext/Blend/Val", ["A", float(a_value)])
            vmc_client.send_message("/VMC/Ext/Blend/Apply", [])
        lipsync_sent += 1
    except Exception as exc:
        lipsync_errors += 1
        print(f"    LIPSYNC ERROR: {exc}")

# --- Threads (exactly like ritsu.py _play_wav) ---
print(f"\n[3] Playing + lip sync (concurrent, like ritsu.py) ...")

audio_results = {}

def _lipsync_sender():
    frame_interval = 0.033
    total = len(lip_values)
    print(f"    [lipsync] START: {total} frames")
    t0 = time.time()
    for idx, val in enumerate(lip_values):
        target_time = t0 + idx * frame_interval
        wait = target_time - time.time()
        if wait > 0:
            time.sleep(wait)
        send_lipsync(val)
        if idx < 3 or idx % 30 == 0 or idx == total - 1:
            elapsed = time.time() - t0
            bar = "#" * int(val * 30)
            print(f"    [lipsync] [{idx:3d}/{total}] A={val:.3f} t={elapsed:.2f}s |{bar}")
    # reset
    with vmc_lock:
        vmc_client.send_message("/VMC/Ext/Blend/Val", ["A", 0.0])
        vmc_client.send_message("/VMC/Ext/Blend/Apply", [])
    elapsed = time.time() - t0
    print(f"    [lipsync] END: sent {lipsync_sent} in {elapsed:.2f}s "
          f"(errors={lipsync_errors})")

def _play_single(dev_idx):
    dev_name = sd.query_devices(dev_idx)["name"]
    t0 = time.time()
    try:
        with sd.OutputStream(samplerate=rate, channels=ch,
                             dtype="float32", device=dev_idx) as stream:
            stream.write(audio_f)
        elapsed = time.time() - t0
        remaining = duration - elapsed
        if remaining > 0.05:
            time.sleep(remaining)
        audio_results[dev_idx] = f"OK ({time.time()-t0:.1f}s)"
        print(f"    [audio] [{dev_idx}] {dev_name} done ({time.time()-t0:.1f}s)")
    except Exception as exc:
        audio_results[dev_idx] = f"FAILED: {exc}"
        print(f"    [audio] [{dev_idx}] {dev_name} FAILED: {exc}")

# Launch all (lip sync first, then audio — same order as ritsu.py)
threads = []
threads.append(threading.Thread(target=_lipsync_sender, daemon=True, name="lipsync"))
for d in targets:
    threads.append(threading.Thread(target=_play_single, args=(d,), daemon=True))

t_start = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join()
total_time = time.time() - t_start

print(f"\n[4] Results:")
print(f"    Total time: {total_time:.2f}s")
print(f"    Lipsync: sent={lipsync_sent}, errors={lipsync_errors}")
for idx in targets:
    print(f"    Audio [{idx}]: {audio_results.get(idx, '?')}")
print(f"\n{'PASS' if lipsync_sent == len(lip_values) and lipsync_errors == 0 else 'FAIL'}")
