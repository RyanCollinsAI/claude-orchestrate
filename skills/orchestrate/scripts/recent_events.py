"""Everything a session did after a moment.

  py recent_events.py <sid-prefix> <ISO-timestamp>
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib as L

sid, since = sys.argv[1], sys.argv[2]
with open(L.find_log(sid), "rb") as fh:
    fh.seek(0, 2)
    fh.seek(max(0, fh.tell() - 20000))
    data = fh.read().decode("utf-8", "replace")

for l in data.splitlines():
    if not l.startswith("{"):
        continue
    try:
        e = json.loads(l)
    except Exception:
        continue
    if e.get("timestamp", "") < since:
        continue
    t = e.get("type")
    m = e.get("message", {})
    c = m.get("content") if isinstance(m, dict) else None
    if isinstance(c, list):
        c = " ".join((b.get("text") or ("[tool:" + b.get("name", "") + "]")
                      if b.get("type") != "tool_result" else "[tool_result]")
                     for b in c if isinstance(b, dict))
    print(t, e.get("subtype", ""), (e.get("content") or c or "")[:500].replace("\n", " "))
