#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VMC Lip Sync Test — VMagicMirror(:39539)にA/I/U/E/O BlendShapeを送信

Usage: python test_vmc_expression.py [host] [port]

VMagicMirror側のリップシンク設定はオフにしてからテストすること。
"""
import sys
import time

try:
    from pythonosc.udp_client import SimpleUDPClient
except ImportError:
    print("ERROR: pythonosc not installed. Run: pip install python-osc")
    sys.exit(1)

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 39539

client = SimpleUDPClient(HOST, PORT)

VOWELS = ["A", "I", "U", "E", "O"]


def send(name: str, value: float) -> None:
    client.send_message("/VMC/Ext/Blend/Val", [name, float(value)])
    client.send_message("/VMC/Ext/Blend/Apply", [])


def reset() -> None:
    for v in VOWELS:
        client.send_message("/VMC/Ext/Blend/Val", [v, 0.0])
    client.send_message("/VMC/Ext/Blend/Apply", [])


# =====================================================================
print(f"VMC Lip Sync Test → {HOST}:{PORT}")
print("=" * 50)

# --- Test 1: A BlendShape open/close cycle ---------------------------
print()
print("[Test 1] A BlendShape open/close (2s intervals)")
print("  Sequence: 0.0 → 0.8 → 0.0 → 0.6 → 0.0")
print()

sequence = [0.0, 0.8, 0.0, 0.6, 0.0]
for i, val in enumerate(sequence):
    bar = "#" * int(val * 30)
    print(f"  A = {val:.1f}  |{bar}")
    send("A", val)
    if i < len(sequence) - 1:
        time.sleep(2.0)

reset()
time.sleep(1.0)

# --- Test 2: Vowel cycle (A → I → U → E → O) -----------------------
print()
print("[Test 2] Vowel cycle: A → I → U → E → O (2s each)")
print()

for vowel in VOWELS:
    print(f"  {vowel} = 0.8  ", end="", flush=True)
    reset()
    send(vowel, 0.8)
    time.sleep(2.0)
    print("ok")

reset()
time.sleep(1.0)

# --- Test 3: Smooth A open/close animation ---------------------------
print()
print("[Test 3] Smooth A animation (30fps, 3s)")

steps = 90  # 30fps x 3s
for i in range(steps):
    # sine wave: 0→1→0→1→0→1→0 over 3 seconds
    import math
    val = abs(math.sin(i / steps * math.pi * 3))
    send("A", round(val, 2))
    time.sleep(1.0 / 30)

reset()

# =====================================================================
print("  done")
print()
print("=" * 50)
print("All tests complete. Mouth reset to closed.")
