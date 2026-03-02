from __future__ import annotations
from pathlib import Path
import datetime, re

p = Path("/opt/agents/ritsu/app.py")
txt = p.read_text(encoding="utf-8")

bak = p.with_name(f"app.py.bak.v2_actions_out_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}")
bak.write_text(txt, encoding="utf-8")

# 既に入ってたら何もしない
if 'actions_out.append({"type":"speak"' in txt:
    raise SystemExit("No change: actions_out already present.")

# assistant_v2 の return 直前に actions_out を作って、return の actions= を差し替える
m = re.search(r"(?m)^(?P<ind>\s*)return\s+AssistantV2Response\(", txt)
if not m:
    raise SystemExit("ERROR: cannot find 'return AssistantV2Response('")

ind = m.group("ind")
insert = (
    f"{ind}# PhaseX: actions_out (client-side)\n"
    f"{ind}should_speak = True\n"
    f"{ind}actions_out: List[Dict[str, Any]] = []\n"
    f"{ind}if reply_text and should_speak:\n"
    f"{ind}    actions_out.append({{\"type\":\"speak\",\"payload\":{{\"text\": reply_text}}}})\n"
    f"{ind}if emotion_tag and emotion_tag != \"neutral\":\n"
    f"{ind}    actions_out.append({{\"type\":\"emotion\",\"payload\":{{\"tag\": emotion_tag}}}})\n"
    f"\n"
)

pos = m.start()
txt2 = txt[:pos] + insert + txt[pos:]

# return の actions を actions_out にする（actions=... の行が無い場合は追加）
if re.search(r"actions\s*=", txt2[m.start():m.start()+2000]):
    txt2 = re.sub(r"(return\s+AssistantV2Response\([\s\S]*?)actions\s*=\s*[^,\n]+",
                  r"\1actions=actions_out",
                  txt2, count=1)
else:
    txt2 = re.sub(r"(return\s+AssistantV2Response\([\s\S]*?)debug\s*=",
                  r"\1actions=actions_out,\n\g<1>debug=",
                  txt2, count=1)

p.write_text(txt2, encoding="utf-8")
print(f"OK: patched app.py (backup: {bak})")
