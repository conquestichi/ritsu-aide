#!/usr/bin/env python3
"""Test lip sync using ritsu.py's actual TTSEngine + VMCClient classes.

This imports the real classes and exercises the exact code path.
"""
import sys
import time
import os

# Set env so ritsu.py doesn't complain
os.environ.setdefault("RITSU_BEARER_TOKEN", "test")
os.environ.setdefault("RITSU_BASE_URL", "http://127.0.0.1:18181")

# Import from ritsu.py
sys.path.insert(0, os.path.dirname(__file__))
from ritsu import VMCClient, TTSEngine, log

print("=" * 60)
print("ritsu.py TTSEngine + VMCClient lip sync test")
print("=" * 60)

# Create VMCClient
vmc = VMCClient(
    host=os.environ.get("RITSU_VMC_HOST", "127.0.0.1"),
    port=int(os.environ.get("RITSU_VMC_PORT", "39539")),
    expr_map_path="vmc_expr_map.json",
)
print(f"VMC: {vmc.host}:{vmc.port}")

# Create TTSEngine (connects to VOICEVOX)
tts = TTSEngine(
    base_url=os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021"),
    default_style_id=0,
)
tts._vmc = vmc  # wire up lip sync (same as main())
print(f"TTS: {tts.base}")
print(f"  _vmc set: {tts._vmc is not None}")
print(f"  cable_idx: {tts._cable_idx}")
print(f"  speaker_idx: {tts._speaker_idx}")

# Test speak
TEXT = "今日もよろしくお願いします。"
print(f"\nSpeaking: {TEXT!r}")
print("Watch VMagicMirror mouth and [LIPSYNC] logs below:")
print("-" * 60)

tts.speak(TEXT)

# Wait for TTS queue to be processed
# speak() just puts text in queue, _worker processes it
time.sleep(1)  # wait for synthesis to start
while tts.speaking.is_set():
    time.sleep(0.1)
# Extra wait for any remaining audio
time.sleep(1)

print("-" * 60)
print("Test complete.")
