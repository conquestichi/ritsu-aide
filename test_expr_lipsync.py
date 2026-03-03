#!/usr/bin/env python3
"""Test: happy expression + TTS playback simultaneously.

Verifies that:
  - send_expression("happy") stores _active_expr (deferred during TTS)
  - Lipsync thread bundles Val[A] + Val[Joy] + Apply in each frame
  - Mouth moves AND expression shows on avatar at the same time

Usage:
  python test_expr_lipsync.py

Requires: VOICEVOX running on localhost:50021, VMagicMirror on port 39539.
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent))

# Load env
env_path = Path(__file__).parent / ".ritsu_worker.env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from ritsu import VMCClient, TTSEngine, log, _init_log

_init_log()

def main():
    script_dir = Path(__file__).parent
    map_path = str(script_dir / "vmc_expr_map.json")

    # VMC
    vmc = VMCClient(
        host=os.environ.get("RITSU_VMC_HOST", "127.0.0.1"),
        port=int(os.environ.get("RITSU_VMC_PORT", "39539")),
        expr_map_path=map_path,
    )
    log(f"[TEST] VMC → {vmc.host}:{vmc.port}")
    log(f"[TEST] expr_map = {json.dumps(vmc.expr_map, ensure_ascii=False)}")

    # TTS
    tts = TTSEngine(
        base_url=os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021"),
        default_style_id=int(os.environ.get("RITSU_VOICEVOX_STYLE_ID", "0")),
    )
    tts._vmc = vmc
    vmc._speaking_event = tts.speaking
    log("[TEST] TTS + VMC wired")

    # ---- Test 1: Standalone expression (no TTS) ----
    log("\n===== Test 1: Standalone expression (no TTS) =====")
    log("[TEST] Sending happy expression (standalone, should Val+Apply+hold+fade)")
    vmc.send_expression("happy", value=1.0, hold_ms=1500, fade_ms=500)
    time.sleep(3)
    log(f"[TEST] _active_expr after fade: {vmc._active_expr}")

    # ---- Test 2: Expression + TTS simultaneously ----
    log("\n===== Test 2: Expression + TTS simultaneously =====")
    test_text = "やっほー！今日もいい天気だね。一緒に遊ぼうよ！"
    log(f"[TEST] Speaking: {test_text}")

    # Start TTS first
    tts.speak(test_text)
    # Small delay so TTS worker picks up and sets speaking flag
    time.sleep(0.3)

    # Now send expression — should detect TTS active and defer
    log(f"[TEST] tts.speaking.is_set() = {tts.speaking.is_set()}")
    vmc.send_expression("happy", value=1.0, hold_ms=2000, fade_ms=500)
    log(f"[TEST] _active_expr = {vmc._active_expr}")

    # Wait for TTS to finish
    log("[TEST] Waiting for TTS to finish...")
    while tts.speaking.is_set():
        time.sleep(0.1)
    log("[TEST] TTS done")
    log(f"[TEST] _active_expr after TTS: {vmc._active_expr}")

    # Expression should still be on avatar (persists after TTS)
    time.sleep(1)

    # ---- Test 3: Neutral reset ----
    log("\n===== Test 3: Reset to neutral =====")
    vmc.send_expression("neutral", value=1.0, hold_ms=1000, fade_ms=300)
    time.sleep(2)

    log("\n===== All tests complete =====")

if __name__ == "__main__":
    main()
