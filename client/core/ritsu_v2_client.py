# ritsu_v2_client.py
import os, sys, json, tempfile, argparse
import requests


RITSU_URL = os.environ.get("RITSU_URL", "http://127.0.0.1:8181/assistant/v2").rstrip("/")
RITSU_TOKEN = os.environ.get("RITSU_BEARER_TOKEN", "")

VOICEVOX_BASE_URL = os.environ.get("VOICEVOX_BASE_URL", "http://127.0.0.1:50021").rstrip("/")
VOICEVOX_SPEAKER = int(os.environ.get("VOICEVOX_SPEAKER", "2"))

EMOTION_PATH = os.environ.get("RITSU_EMOTION_PATH", os.path.join(os.getcwd(), "emotion_tag.txt"))
NO_SPEAK = os.environ.get("RITSU_NO_SPEAK", "0") in ("1", "true", "TRUE", "yes", "YES")

def eprint(*a):
    print(*a, file=sys.stderr, flush=True)

def voicevox_speak(text: str):
    text = (text or "").strip()
    if not text:
        return
    try:
        q = requests.post(
            f"{VOICEVOX_BASE_URL}/audio_query",
            params={"text": text, "speaker": VOICEVOX_SPEAKER},
            timeout=10,
        )
        q.raise_for_status()
        s = requests.post(
            f"{VOICEVOX_BASE_URL}/synthesis",
            params={"speaker": VOICEVOX_SPEAKER},
            data=q.content,
            timeout=30,
        )
        s.raise_for_status()

        import winsound
        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with open(wav_path, "wb") as f:
            f.write(s.content)
        winsound.PlaySound(wav_path, winsound.SND_FILENAME)
        try:
            os.remove(wav_path)
        except Exception:
            pass
    except Exception as ex:
        eprint(f"[warn] VOICEVOX failed: {type(ex).__name__}: {ex}")

def write_emotion(tag: str):
    try:
        with open(EMOTION_PATH, "w", encoding="utf-8") as f:
            f.write(tag or "")
    except Exception as ex:
        eprint(f"[warn] write emotion failed: {type(ex).__name__}: {ex}")

def run_actions(actions: list, fallback_reply: str, fallback_emo: str):
    actions = actions or []
    if not actions:
        actions = [
            {"type": "emotion", "payload": {"tag": fallback_emo}},
            {"type": "speak", "payload": {"text": fallback_reply}},
        ]

    for a in actions:
        t = (a.get("type") or "").strip()
        p = a.get("payload") or {}
        if t == "emotion":
            write_emotion(p.get("tag", "neutral"))
        elif t == "speak":
            if NO_SPEAK:
                continue
            voicevox_speak(p.get("text", p.get("message","")))

        elif t == "log":
            eprint(f"[log] {p.get('message','')}")
        elif t == "notify":
            eprint(f"[notify] {p.get('text', p.get('message',''))}")
        else:
            eprint(f"[warn] unknown action={t}")

def call_ritsu(conversation_id: str, text: str, actions_in=None):
    headers = {"Content-Type": "application/json"}
    if RITSU_TOKEN:
        headers["Authorization"] = f"Bearer {RITSU_TOKEN}"

    payload = {"conversation_id": conversation_id, "text": text}
    if actions_in is not None:
        payload["actions_in"] = actions_in

    r = requests.post(RITSU_URL, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default="win")
    ap.add_argument("--stdin", action="store_true", help="read text from stdin")
    ap.add_argument("text", nargs="*")
    ns = ap.parse_args()

    # ★重要：--cid だけ来ても stdin を読む（AHKの < qFile 対応）
    text = " ".join(ns.text).strip()
    if ns.stdin or (not text):
        text = sys.stdin.read().strip()

    if not text:
        eprint("usage: py ritsu_v2_client.py [--cid CID] [--stdin] TEXT")
        return 2

    try:
        resp = call_ritsu(ns.cid, text)
    except Exception as ex:
        eprint(f"[error] call failed: {type(ex).__name__}: {ex}")
        return 1

    reply = resp.get("reply_text", "") or ""
    emo = resp.get("emotion_tag", "neutral") or "neutral"
    actions = resp.get("actions", []) or []

    # stdoutは「返信テキストだけ」(AHKが読む)
    print(reply, flush=True)

    # actionsはここで処理（NO_SPEAKなら喋らず、emotionだけ更新できる）
    run_actions(actions, fallback_reply=reply, fallback_emo=emo)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
