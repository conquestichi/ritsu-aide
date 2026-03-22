from __future__ import annotations
from pathlib import Path
import datetime
import re

p = Path("/opt/agents/ritsu/worker_actions.py")
txt = p.read_text(encoding="utf-8")
bak = p.with_name(f"worker_actions.py.bak.{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}")
bak.write_text(txt, encoding="utf-8")

# 1) utcnow警告対策（timezone追加＆now(timezone.utc)へ）
if "from datetime import datetime, timezone" not in txt:
    txt = re.sub(r"from datetime import datetime\s*\n", "from datetime import datetime, timezone\n", txt, count=1)
txt = txt.replace('datetime.utcnow().replace(microsecond=0).isoformat(sep=" ")',
                  'datetime.now(timezone.utc).replace(microsecond=0).isoformat(sep=" ")')
txt = txt.replace("datetime.utcnow().replace(microsecond=0).isoformat(sep=' ')",
                  "datetime.now(timezone.utc).replace(microsecond=0).isoformat(sep=' ')")

# 2) notify用import（なければ追加）
if "from urllib.request import Request, urlopen" not in txt:
    txt = re.sub(r"(import traceback\s*\n)",
                 r"\1from urllib.request import Request, urlopen\nfrom urllib.error import URLError, HTTPError\n",
                 txt, count=1)

# 3) execute_action に speak/notify を追加（logブロック直後に挿入）
pat_log_block = r'(if t == "log":\n(?:[ \t].*\n)+?[ \t]*return\n)'
m = re.search(pat_log_block, txt)
if not m:
    raise SystemExit("ERROR: cannot find log action block to patch")

insert = """
    if t == "speak":
        # payload: {"text": "...", ...}
        text = p.get("text", p.get("message", ""))
        if not isinstance(text, str):
            text = str(text)
        out_path = os.environ.get("RITSU_TTS_QUEUE_PATH", "/srv/ritsu/state/tts_queue.jsonl")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        item = {"ts": datetime.now(timezone.utc).isoformat(), "text": text, "payload": p}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\\n")
        log_line(f"action speak id={a.id} req={a.request_id} text={text[:200]}")
        return

    if t == "notify":
        # payload: {"text": "..."}  (Slack webhookがあれば送る/なければログだけ)
        text = p.get("text", p.get("message", ""))
        if not isinstance(text, str):
            text = str(text)
        webhook = (os.environ.get("SLACK_WEBHOOK_URL")
                   or os.environ.get("RITSU_SLACK_WEBHOOK")
                   or os.environ.get("INKARITSU_SLACK_WEBHOOK"))
        if webhook:
            body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
            req = Request(webhook, data=body, headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(req, timeout=10) as resp:
                    _ = resp.read()
                log_line(f"action notify(slack) id={a.id} req={a.request_id} ok")
            except (HTTPError, URLError, Exception) as e:
                log_line(f"action notify(slack) id={a.id} req={a.request_id} err={type(e).__name__}:{e}")
        else:
            log_line(f"action notify(no-webhook) id={a.id} req={a.request_id} text={text[:200]}")
        return
"""
txt = txt[:m.end()] + insert + txt[m.end():]

# 4) processed=0 のexitログ連発を抑制（processed>0 or --once の時だけ出す）
txt = re.sub(r'(\n\s*)log_line\(f"exit worker_id=\{args\.worker_id\} processed=\{processed\}"\)\n',
             r'\1if processed or args.once:\n\1    log_line(f"exit worker_id={args.worker_id} processed={processed}")\n',
             txt, count=1)

p.write_text(txt, encoding="utf-8")
print(f"OK: patched worker_actions.py (backup: {bak})")
