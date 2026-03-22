import os, sys, json, time, argparse, urllib.request, urllib.parse
from pathlib import Path

DEFAULT_BASE = os.getenv("VOICEVOX_URL", "http://127.0.0.1:50021")
DEFAULT_SPEAKER_NAME = os.getenv("RITSU_VOICEVOX_SPEAKER_NAME", "四国めたん")

# V1仕様（セクシーは控えめに調整）
PRESETS = {
    "amaama": {"speed": 0.98, "pitch":  0.00, "intonation": 1.20, "volume": 1.00},
    # 旧: speed0.92/pitch-0.04/intonation1.05/volume0.95 は強い → 控えめに寄せる
    'sexy': {'speed': 0.96, 'pitch': -0.02, 'intonation': 1.10, 'volume': 0.98},
}

def http_get(url: str, timeout=3) -> bytes:
    return urllib.request.urlopen(url, timeout=timeout).read()

def http_post(url: str, data=b"", headers=None, timeout=10) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    return urllib.request.urlopen(req, timeout=timeout).read()

def load_speakers(base: str):
    return json.loads(http_get(f"{base}/speakers", timeout=3).decode("utf-8"))

def resolve_style_id(base: str, speaker_name: str, style_name: str) -> int:
    speakers = load_speakers(base)
    # ゆるめ一致（完全一致→部分一致）
    cand = None
    for sp in speakers:
        if sp.get("name") == speaker_name:
            cand = sp; break
    if cand is None:
        for sp in speakers:
            if speaker_name in sp.get("name",""):
                cand = sp; break
    if cand is None:
        raise SystemExit(f"[NG] speaker not found: {speaker_name}")

    styles = cand.get("styles") or []
    for st in styles:
        if st.get("name") == style_name:
            return int(st["id"])
    # style名が違う環境があるので、最後の保険：最初のstyle
    return int(styles[0]["id"]) if styles else int(cand.get("speaker_uuid", 0))

def is_sexy_auto(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    # 「短い・語尾・甘めワード」寄りをセクシーに倒す（やり過ぎない）
    sexy_words = ("ねえ", "だよ", "かな", "お願い", "だめ", "すき", "好き", "おはよ", "おやすみ")
    if any(w in t for w in sexy_words) and len(t) <= 28:
        return True
    if t.endswith(("…", "。", "？", "?")) and len(t) <= 22:
        return True
    return False

def split_text(text: str):
    # 句点/改行優先で小分け + ポーズ秒
    s = (text or "").replace("\r\n","\n").replace("\r","\n").strip()
    if not s:
        return []
    parts = []
    buf = []
    for ch in s:
        buf.append(ch)
        if ch in ("。","！","!","？","?","\n"):
            p = "".join(buf).strip()
            if p:
                parts.append(p)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)

    out = []
    for i, p in enumerate(parts):
        p2 = p.strip()
        if not p2:
            continue
        if p2.endswith("。") or p2.endswith("…"):
            pause = 0.45
        elif p2.endswith("！") or p2.endswith("!") or p2.endswith("？") or p2.endswith("?"):
            pause = 0.30
        elif p2.endswith("\n"):
            pause = 0.20
        else:
            pause = 0.15
        out.append((p2.replace("\n"," ").strip(), pause))
    return out

def apply_preset(aq_obj: dict, preset: dict) -> dict:
    aq_obj["speedScale"] = float(preset["speed"])
    aq_obj["pitchScale"] = float(preset["pitch"])
    aq_obj["intonationScale"] = float(preset["intonation"])
    aq_obj["volumeScale"] = float(preset["volume"])
    return aq_obj

def synth_wav(base: str, text: str, style_id: int, preset: dict) -> bytes:
    q = urllib.parse.urlencode({"text": text, "speaker": style_id})
    # VOICEVOXは audio_query が POST（ここが405の原因になりがち）
    aq = http_post(f"{base}/audio_query?{q}", data=b"", timeout=10)
    aq_obj = json.loads(aq.decode("utf-8"))
    aq_obj = apply_preset(aq_obj, preset)
    body = json.dumps(aq_obj, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type":"application/json"}
    return http_post(f"{base}/synthesis?speaker={style_id}", data=body, headers=headers, timeout=60)

def play_wav(path: str):
    import winsound
    winsound.PlaySound(path, winsound.SND_FILENAME)

def read_clipboard_text():
    try:
        import pyperclip
        return (pyperclip.paste() or "").strip()
    except Exception:
        raise SystemExit("[NG] clipboard requires pyperclip (pip install pyperclip)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text_pos", nargs="?", default=None, help="text (positional)")
    # 互換：--text は「ファイル/文字列どちらでも」受ける（既存の呼び出しを壊さない）
    ap.add_argument("--text", dest="text_opt", default=None, help="text OR path to text file (compat)")
    ap.add_argument("--text_file", "--text-file", dest="text_file", default=None, help="path to text file")
    ap.add_argument("--clipboard", action="store_true")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--speaker-name","--speaker_name", default=DEFAULT_SPEAKER_NAME)
    ap.add_argument("--style", choices=["auto","amaama","sexy"], default=os.getenv("RITSU_TTS_STYLE","auto"))
    ap.add_argument("--mode", choices=["auto","amaama","sexy"], default=None, help="alias of --style")
    ap.add_argument("--out", default=str(Path(os.getenv("TEMP",".")).joinpath("ritsu_tts_out.wav")))
    ap.add_argument("--no-play", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    style_sel = args.mode or args.style

    text = ""
    if args.clipboard:
        text = read_clipboard_text()
    elif args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8", errors="ignore").strip()
    elif args.text_opt:
        p = Path(args.text_opt)
        if p.exists() and p.is_file():
            text = p.read_text(encoding="utf-8", errors="ignore").strip()
        else:
            text = str(args.text_opt).strip()
    elif args.text_pos:
        text = str(args.text_pos).strip()

    if not text:
        raise SystemExit("[NG] no text")

    # 到達性チェック
    http_get(f"{args.base}/speakers", timeout=3)

    chunks = split_text(text)
    out_path = str(Path(args.out))

    for c, pause in chunks:
        style_name = "セクシー" if (style_sel == "sexy" or (style_sel == "auto" and is_sexy_auto(c))) else "あまあま"
        preset = PRESETS["sexy"] if style_name == "セクシー" else PRESETS["amaama"]
        style_id = resolve_style_id(args.base, args.speaker_name, style_name)

        if args.debug:
            print(f"[TTS] speaker='{args.speaker_name}' style='{style_name}' id={style_id} preset={preset}", file=sys.stderr)

        if not args.dry_run:
            wav = synth_wav(args.base, c, style_id, preset)
            Path(out_path).write_bytes(wav)
            if not args.no_play:
                play_wav(out_path)
        time.sleep(pause)

    print("[OK] spoke:", args.speaker_name, "style=", style_sel, "out=", out_path)
    return 0

# --- RITSU_VOICE_PRESET_LOCK ---
# V1: 読み上げは VOICEVOX（四国めたん）運用
DEFAULT_SPEAKER_NAME = "四国めたん"

# 「ちょうどいい」固定版：控えめセクシー（強すぎ対策）
# ※ tts_speak.py の PRESETS を上書き（存在しない場合は新規作成）
_SEXY = {"speed":1.03,"pitch":0.00,"intonationScale":1.02,"volume":0.95}
try:
    PRESETS["sexy"].update(_SEXY)
except Exception:
    try:
        PRESETS["sexy"] = dict(_SEXY)
    except Exception:
        pass
# --- RITSU_VOICE_PRESET_LOCK ---
if __name__ == "__main__":
    raise SystemExit(main())
