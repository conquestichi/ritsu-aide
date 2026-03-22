"""最小PTTテスト — V3パターンとsd.recパターンを両方試す"""
import sounddevice as sd
import soundfile as sf
import numpy as np
import wave
import queue
import time
import os

print("=== Audio devices ===")
for i, d in enumerate(sd.query_devices()):
    if d['max_input_channels'] > 0:
        default = " <<<DEFAULT" if i == sd.default.device[0] else ""
        print(f"  #{i}: {d['name']} (rate={d['default_samplerate']:.0f}){default}")

print()
dev = sd.default.device[0]
info = sd.query_devices(dev)
native_rate = int(info['default_samplerate'])
print(f"Using device #{dev}: {info['name']} native_rate={native_rate}")

# --- Test 1: sd.rec() + sd.wait() (proven working) ---
print("\n=== Test 1: sd.rec() + sd.wait() (2s) ===")
audio1 = sd.rec(int(native_rate * 2), samplerate=native_rate, channels=1, dtype="int16", device=dev)
sd.wait()
peak1 = int(np.max(np.abs(audio1)))
print(f"  peak={peak1} {'OK' if peak1 > 100 else 'SILENT'}")

# --- Test 2: V3 exact pattern (InputStream + callback + 16kHz) ---
print("\n=== Test 2: V3 InputStream callback (16kHz, 3s) ===")
q = queue.Queue()
cb_count = [0]
def cb(indata, frames, time_info, status):
    cb_count[0] += 1
    q.put(indata.copy())

chunks2 = []
try:
    with sd.InputStream(samplerate=16000, channels=1, dtype="int16", callback=cb):
        t0 = time.time()
        while time.time() - t0 < 3:
            try:
                data = q.get(timeout=0.3)
                chunks2.append(data)
            except:
                pass
    if chunks2:
        audio2 = np.concatenate(chunks2).flatten()
        peak2 = int(np.max(np.abs(audio2)))
    else:
        peak2 = 0
    print(f"  callbacks={cb_count[0]} chunks={len(chunks2)} peak={peak2} {'OK' if peak2 > 100 else 'SILENT'}")
except Exception as e:
    print(f"  ERROR: {e}")

# --- Test 3: InputStream callback at native rate ---
print(f"\n=== Test 3: InputStream callback ({native_rate}Hz, 3s) ===")
q3 = queue.Queue()
cb3_count = [0]
def cb3(indata, frames, time_info, status):
    cb3_count[0] += 1
    q3.put(indata.copy())

chunks3 = []
try:
    with sd.InputStream(samplerate=native_rate, channels=1, dtype="int16", callback=cb3):
        t0 = time.time()
        while time.time() - t0 < 3:
            try:
                data = q3.get(timeout=0.3)
                chunks3.append(data)
            except:
                pass
    if chunks3:
        audio3 = np.concatenate(chunks3).flatten()
        peak3 = int(np.max(np.abs(audio3)))
    else:
        peak3 = 0
    print(f"  callbacks={cb3_count[0]} chunks={len(chunks3)} peak={peak3} {'OK' if peak3 > 100 else 'SILENT'}")
except Exception as e:
    print(f"  ERROR: {e}")

# --- Test 4: sd.rec() chunk loop (0.5s x 6) ---
print("\n=== Test 4: sd.rec() chunk loop (0.5s x 6) ===")
chunks4 = []
for i in range(6):
    c = sd.rec(int(native_rate * 0.5), samplerate=native_rate, channels=1, dtype="int16", device=dev)
    sd.wait()
    chunks4.append(c.copy())
    p = int(np.max(np.abs(c)))
    print(f"  chunk {i}: peak={p}")
audio4 = np.concatenate(chunks4).flatten()
peak4 = int(np.max(np.abs(audio4)))
print(f"  total peak={peak4} {'OK' if peak4 > 100 else 'SILENT'}")

print("\n=== Done ===")
