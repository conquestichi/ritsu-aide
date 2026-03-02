import argparse
import os
import sys
import tempfile
import wave
from pathlib import Path

def eprint(*a):
    print(*a, file=sys.stderr)

def read_key() -> str:
    key_path = Path(os.path.expanduser("~")) / ".ritsu" / "openai_key.txt"
    if not key_path.exists():
        raise RuntimeError(f"openai_key.txt not found: {key_path}")
    key = key_path.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError("openai_key.txt is empty")
    return key

def record_wav(out_wav: str, sec: float, rate: int = 16000):
    import sounddevice as sd  # pip install sounddevice
    channels = 1
    frames = int(rate * sec)
    audio = sd.rec(frames, samplerate=rate, channels=channels, dtype="int16")
    sd.wait()
    with wave.open(out_wav, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # int16
        wf.setframerate(rate)
        wf.writeframes(audio.tobytes())

def transcribe(wav_path: str, api_key: str, model: str) -> str:
    from openai import OpenAI  # pip install --upgrade openai
    client = OpenAI(api_key=api_key)
    with open(wav_path, "rb") as f:
        t = client.audio.transcriptions.create(model=model, file=f)
    text = getattr(t, "text", None)
    if not text:
        if isinstance(t, dict) and "text" in t:
            text = t["text"]
        else:
            text = str(t)
    return text.strip()

def write_text(p: str, s: str):
    if not p:
        return
    Path(p).write_text(s, encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sec", type=float, default=3.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--err", default="")
    ap.add_argument("--model", default="gpt-4o-mini-transcribe")
    args = ap.parse_args()

    try:
        api_key = read_key()
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "in.wav")
            record_wav(wav, args.sec)
            text = transcribe(wav, api_key, args.model)

        if args.out:
            write_text(args.out, text)
        else:
            print(text)
        return 0

    except Exception as ex:
        msg = f"[stt_error] {ex}"
        if args.err:
            write_text(args.err, msg)
        else:
            eprint(msg)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())