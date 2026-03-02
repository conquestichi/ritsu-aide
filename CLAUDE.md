# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Ritsu (律)** — Japanese virtual assistant unified client that integrates TTS, STT, VMC avatar control, and backend polling into a single Python process.

This is version 3 (v3), which consolidates 20+ legacy scripts (AHK/PS1/CMD/Python) into `ritsu.py`.

## Setup & Development Commands

### Initial Setup
```cmd
setup.cmd
```
- Installs Python dependencies from `requirements.txt`
- Creates `.ritsu_worker.env` from `env.v3.example`
- After setup, edit `.ritsu_worker.env` to configure:
  - `RITSU_BEARER_TOKEN` (required)
  - `RITSU_SSH_HOST` (if using SSH tunnel to VPS)
  - OpenAI API key at `~/.ritsu/openai_key.txt` (for STT)

### Running
```cmd
ritsu.cmd              # Launch GUI mode (default)
python ritsu.py        # Same as above
python ritsu.py --no-gui  # Worker-only mode (no GUI, runs as background service)
python ritsu.py --no-tunnel  # Disable SSH tunnel
```

### Dependencies
- **VOICEVOX** (http://127.0.0.1:50021) — TTS engine for Japanese speech synthesis
- **VMagicMirror** or compatible VMC receiver (port 39539) — Avatar expression control
- **VPS backend** (configured in env) — Action queue server

## Architecture

### Thread Model (ritsu.py:6-15)
```
[Main Thread: tkinter GUI]
     ↕ Queue
[API Thread]      → VPS :8181 (HTTP direct)
[Worker Thread]   → Action polling → VMC/TTS/Notify
[Monologue Thread]→ Idle detect → /assistant/v2
[Tunnel Thread]   → SSH tunnel management
[TTS Thread]      → VOICEVOX synthesis + playback
```

### Core Components

**RitsuAPI** (ritsu.py:159-222)
- HTTP client for VPS backend communication
- Endpoints: `/assistant/text`, `/assistant/v2`, `/actions/next`, `/actions/done`, `/actions/failed`

**WorkerThread** (ritsu.py:503-555)
- Polls `/actions/next?worker_id=...` every 1 second (configurable)
- Dispatches actions: `notify`, `emotion`/`vmc_expression`, `speak`, `gesture`
- Reports completion or failure back to server

**MonologueThread** (ritsu.py:559-623)
- Idle detection via Windows API (`GetLastInputInfo`)
- Conditional triggers: idle time, cooldown, daily cap, time range, active window suppression
- Generates contextual monologue via `/assistant/v2` endpoint
- Executes locally (not enqueued as actions)

**TTSEngine** (ritsu.py:286-376)
- VOICEVOX integration with dual presets: `amaama` (甘々) and `sexy` (セクシー)
- Auto-detects sexy mode based on content (short phrases with specific keywords)
- Chunk splitting on Japanese sentence boundaries (。！？)
- Background worker thread for non-blocking playback

**VMCClient** (ritsu.py:226-282)
- OSC protocol for VMagicMirror/VSeeFace expression control
- Maps emotion tags to BlendShape names via `vmc_expr_map.json`
- Supports hold + fade for smooth expression transitions

**STTEngine** & **PTTRecorder** (ritsu.py:381-463)
- OpenAI Whisper (`gpt-4o-mini-transcribe`) for speech recognition
- Push-to-talk via mouse XButton2 (side button)
- Records 16kHz mono WAV to temp file

**SSHTunnel** (ritsu.py:102-155)
- Auto-reconnecting SSH port forwarding
- Maps localhost:18181 → remote:8181 (configurable)
- Uses Windows OpenSSH or system ssh

### Configuration (env.v3.example)

Key environment variables:
- `RITSU_BASE_URL` — Backend API base (e.g., http://127.0.0.1:18181)
- `RITSU_BEARER_TOKEN` — Authentication token (required)
- `RITSU_WORKER_ID` — Worker identifier (defaults to hostname)
- `RITSU_SSH_HOST` — SSH tunnel target (optional, leave empty to disable)
- `VOICEVOX_URL` — TTS engine URL
- `RITSU_VMC_HOST` / `RITSU_VMC_PORT` — VMC receiver address
- `RITSU_MONOLOGUE_ENABLE` — Enable/disable monologue system (0 or 1)

### VMC Expression Map (vmc_expr_map.json)

Maps emotion tags to VMagicMirror BlendShape names:
- `happy` → `Joy`
- `sad` → `Sorrow`
- `angry` → `Angry`
- `surprised` → `Surprised`
- `neutral`, `calm`, `think` → `Neutral`

Custom mappings can be added for finer control.

### Hotkeys (GUI Mode)

**Keyboard:**
- **F10** — Toggle GUI visibility
- **Enter** — Send text (Shift+Enter for newline)
- **Esc** — Hide GUI

**Mouse (Windows API hook):**
- **XButton1** (Back button) — Toggle GUI visibility
- **XButton2** (Forward button) — Push-to-talk (hold to record, release to transcribe & send)

**PTT Flow:**
1. Press and hold XButton2 → Recording starts (🎤 録音中...)
2. Speak into microphone
3. Release XButton2 → Recording stops → OpenAI Whisper transcription
4. Transcribed text sent to `/assistant/v2` → TTS playback + VMC expression

**Note:** Mouse buttons use Windows Low-Level Mouse Hook API for reliable detection of XButton1/XButton2 (side buttons). This works globally regardless of window focus.

## Legacy Files

The following files are legacy v2 implementations, largely superseded by `ritsu.py`:

- `ritsu_v2_client.py` — Old client for `/assistant/v2` endpoint
- `ritsu_worker_notify.py` — Old worker polling script (now integrated into WorkerThread)
- `tts_speak.py` — Standalone TTS script (still used by worker for `speak` actions)
- `vmc_send_pyosc.py` — Minimal VMC OSC sender
- Various `.ps1`/`.cmd` wrapper scripts

When modifying functionality, prefer updating `ritsu.py` over legacy scripts.

## Logs

Logs are written to `%LOCALAPPDATA%\RitsuWorker\ritsu_v3_YYYYMMDD_HHMMSS.log`

## Development Notes

- The codebase is Japanese-focused (VOICEVOX speaker: 四国めたん)
- All TTS text processing assumes Japanese sentence structure
- Monologue system uses Windows idle detection and window title inspection
- Expression refinement logic converts coarse emotion tags into granular laugh tiers (`laugh_weak`, `laugh`, `laugh_strong`) based on text analysis
