#!/usr/bin/env python3
"""CABLE Input + Speaker dual output test via sounddevice.

Both devices are addressed by explicit index — independent of
the Windows default playback device setting.
"""
import io
import json
import sys
import threading
import time
import urllib.request
import urllib.parse
import wave

import numpy as np
import sounddevice as sd

VOICEVOX_URL = "http://127.0.0.1:50021"
TEST_TEXT = "テスト音声です。スピーカーとCABLE Input、両方から聞こえますか？"
SPEAKER_ID = 0


def find_devices() -> tuple[int | None, int | None]:
    """Return (cable_idx, speaker_idx) using MME host API."""
    devices = sd.query_devices()
    cable_idx = None
    speaker_idx = None
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
            if "cable" not in d["name"].lower() and d["max_output_channels"] > 0:
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


def synth(text: str, speaker: int) -> bytes:
    q = urllib.parse.urlencode({"text": text, "speaker": speaker})
    req = urllib.request.Request(
        f"{VOICEVOX_URL}/audio_query?{q}", data=b"", method="POST")
    aq = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    body = json.dumps(aq, ensure_ascii=False).encode("utf-8")
    req2 = urllib.request.Request(
        f"{VOICEVOX_URL}/synthesis?speaker={speaker}",
        data=body, headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req2, timeout=60).read()


def main():
    print("=" * 60)
    print("CABLE Input + Speaker Dual Output Test (sounddevice)")
    print("=" * 60)

    cable_idx, speaker_idx = find_devices()
    for label, idx in [("CABLE Input", cable_idx), ("Speaker", speaker_idx)]:
        if idx is not None:
            d = sd.query_devices(idx)
            print(f"  {label:12s}: [{idx}] {d['name']} (hostapi={d['hostapi']})")
        else:
            print(f"  {label:12s}: NOT FOUND")

    if cable_idx is None and speaker_idx is None:
        print("\nERROR: No output devices found.")
        sys.exit(1)

    # Synthesize
    print(f"\n[1/2] VOICEVOX synth ...")
    try:
        wav_bytes = synth(TEST_TEXT, SPEAKER_ID)
    except Exception as e:
        print(f"ERROR: VOICEVOX connection failed ({e})")
        sys.exit(1)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    audio_f = np.frombuffer(raw, dtype="int16").reshape(-1, ch).astype(np.float32) / 32768.0
    duration = len(audio_f) / rate
    print(f"  OK: {rate}Hz, {ch}ch, {duration:.1f}s")

    # Dual playback
    targets = [i for i in (cable_idx, speaker_idx) if i is not None]
    print(f"\n[2/2] Playing on devices {targets} ...")

    results = {}

    def play(idx: int):
        d = sd.query_devices(idx)
        t0 = time.time()
        try:
            with sd.OutputStream(samplerate=rate, channels=ch,
                                 dtype="float32", device=idx) as stream:
                stream.write(audio_f)
            elapsed = time.time() - t0
            remaining = duration - elapsed
            if remaining > 0.05:
                time.sleep(remaining)
            results[idx] = f"OK ({time.time()-t0:.1f}s)"
        except Exception as e:
            results[idx] = f"FAILED: {e}"

    threads = [threading.Thread(target=play, args=(i,)) for i in targets]
    for t in threads:
        t.start()
    print("  >>> Playing simultaneously ...")
    for t in threads:
        t.join()

    for idx in targets:
        d = sd.query_devices(idx)
        print(f"  [{idx:2d}] {d['name']:45s} {results.get(idx, '?')}")

    print("\n" + "=" * 60)
    all_ok = all("OK" in str(results.get(i, "")) for i in targets)
    print(f"Result: {'PASS' if all_ok else 'FAIL'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
