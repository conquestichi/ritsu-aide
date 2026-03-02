import argparse, os, queue, time
import sounddevice as sd
import soundfile as sf

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--stop", required=True)
    ap.add_argument("--sr", type=int, default=16000)
    ap.add_argument("--ch", type=int, default=1)
    args = ap.parse_args()

    stop_path = args.stop
    wav_path = args.wav
    os.makedirs(os.path.dirname(wav_path), exist_ok=True)
    try:
        if os.path.exists(stop_path):
            os.remove(stop_path)
    except Exception:
        pass

    q = queue.Queue()

    def cb(indata, frames, time_info, status):
        if status:
            # don't spam; just keep running
            pass
        q.put(indata.copy())

    with sf.SoundFile(wav_path, mode="w", samplerate=args.sr, channels=args.ch, subtype="PCM_16") as f:
        with sd.InputStream(samplerate=args.sr, channels=args.ch, dtype="int16", callback=cb):
            while not os.path.exists(stop_path):
                try:
                    data = q.get(timeout=0.2)
                    f.write(data)
                except queue.Empty:
                    continue

if __name__ == "__main__":
    main()