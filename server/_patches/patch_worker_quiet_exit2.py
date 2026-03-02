from __future__ import annotations
from pathlib import Path
import datetime, re

p = Path("/opt/agents/ritsu/worker_actions.py")
txt = p.read_text(encoding="utf-8")

bak = p.with_name(f"worker_actions.py.bak.quiet_exit2_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}")
bak.write_text(txt, encoding="utf-8")

pat = r'^(?P<ind>\s*)log_line\(f"exit worker_id=\{args\.worker_id\} processed=\{processed\}"\)\s*$'
m = re.search(pat, txt, flags=re.M)
if not m:
    raise SystemExit("ERROR: exit log_line pattern not found (no change).")

ind = m.group("ind")
rep = (
    f"{ind}if processed:\n"
    f"{ind}    log_line(f\"exit worker_id={{args.worker_id}} processed={{processed}}\")\n"
)

txt2, n = re.subn(pat, rep, txt, count=1, flags=re.M)
if n != 1:
    raise SystemExit(f"ERROR: unexpected replace count: {n}")

p.write_text(txt2, encoding="utf-8")
print(f"OK: patched worker_actions.py (backup: {bak})")
