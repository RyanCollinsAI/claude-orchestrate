"""First ask, last ask, last words of one session.

  py tail_session.py <sid-prefix> [n-msgs] [max-chars]
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

sid = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
maxlen = int(sys.argv[3]) if len(sys.argv) > 3 else 1500


def text_of(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    out = []
    for b in c or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b["text"])
            elif b.get("type") == "tool_use":
                out.append(f"[tool:{b.get('name')}] " + json.dumps(b.get("input"))[:300])
    return "\n".join(out)


# Must match `.jsonl`, not the same-named subagent directory sitting beside it.
hits = [p for p in os.listdir(L.LOGS) if p.startswith(sid) and p.endswith(".jsonl")]
if not hits:
    sys.exit(f"no log starting with {sid!r} in {L.LOGS}")

msgs = []
with open(os.path.join(L.LOGS, hits[0]), encoding="utf-8", errors="replace") as fh:
    for line in fh:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") not in ("user", "assistant"):
            continue
        txt = text_of(e.get("message", {}))
        if not txt.strip():
            continue
        if e["type"] == "user" and ("<system-reminder>" in txt[:80] or "<command-name>" in txt):
            continue
        msgs.append((e.get("timestamp"), e["type"], txt))

for ts, t, txt in msgs[-n:]:
    print(f"--- {t.upper()} {ts}")
    print(txt[:maxlen])
