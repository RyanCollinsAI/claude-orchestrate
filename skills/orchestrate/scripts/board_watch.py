"""Monitor script: one line whenever your human touches the board.

  Monitor(command='py ".../scripts/board_watch.py"', persistent=true)

`lavish-axi poll` long-polls and then dumps a whole YAML-ish page including a dom_snapshot that is
several thousand characters of noise. This wraps it in a loop, throws the noise away, and prints
one short line per thing they actually did:

  ANSWER q4 | A: Ask box, who gets to use it?      an answered question, already applied to the board
  NOTE div#questions > form:nth-of-type(1) | ...   an annotation on one element
  MESSAGE | ...                                    freeform text they typed to the agent
  LAYOUT 2 warnings                                the browser reported overflow - fix it first
  SESSION ended                                    he closed the Lavish session; the loop exits

On an ANSWER it also runs `board answer`, so the page moves the question into Done by itself and
they are not asked twice.

  py board_watch.py [--agent-reply "text"] [--once]

`--agent-reply` is passed to the FIRST poll only; that is how a reply shows up in the Lavish
conversation pane. Every print flushes or Monitor never sees it.
"""
import json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board as B

NOISE = ("Still waiting", "Long-polling", "[lavish-axi]", "dom_snapshot:", "next_step:")
CTX_RE = re.compile(r"\n\s*Context data:\s*(\{.*)\Z", re.S)
# "Q5 Some title: the answer" - the shape board.py's queuePrompt always builds
SHAPE_RE = re.compile(r"^(?P<id>[Qq]\S+)\s+(?P<title>.*?):\s*(?P<answer>.*)$", re.S)


def run_poll(agent_reply=None):
    args = ["poll", B.HTML] + (["--agent-reply", agent_reply] if agent_reply else [])
    r = subprocess.run(B.lavish_cmd(*args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def split_row(line):
    """One `"a","b",c,"d"` row -> list of fields. json.decoder handles the \\" and \\n escapes that
    lavish uses inside quoted fields; an unquoted field runs to the next comma."""
    out, i, n = [], 0, len(line)
    dec = json.JSONDecoder()
    while i < n:
        while i < n and line[i] in " \t":
            i += 1
        if i < n and line[i] == '"':
            try:
                val, i = dec.raw_decode(line, i)
            except ValueError:
                out.append(line[i:].strip('"'))
                break
            out.append(val)
        else:
            j = line.find(",", i)
            j = n if j < 0 else j
            out.append(line[i:j].strip())
            i = j
        while i < n and line[i] in " \t":
            i += 1
        if i < n and line[i] == ",":
            i += 1
    return out


def parse(out):
    """-> (prompts as dicts, layout_warning count, session ended?)"""
    prompts, warns, ended = [], 0, False
    fields, in_prompts = [], False
    for line in out.splitlines():
        if any(m in line for m in NOISE):
            continue
        head = re.match(r"^(\w+)\[(\d+)\](?:\{([^}]*)\})?:", line.strip())
        if head:
            key, count = head.group(1), int(head.group(2))
            in_prompts = key == "prompts"
            fields = (head.group(3) or "").split(",") if in_prompts else []
            if key == "layout_warnings":
                warns = count
            continue
        if re.match(r"^\s*status:\s*ended", line):
            ended = True
        if line.strip().startswith("layout_warnings:") and "0" not in line:
            warns = max(warns, 1)
        if in_prompts and line.startswith(("  ", "\t")) and line.strip():
            if not line.strip()[0] in '"0123456789':
                in_prompts = False
                continue
            vals = split_row(line.strip())
            prompts.append(dict(zip(fields, vals)))
    return prompts, warns, ended


def read_answer(p):
    """(question id, answer) from one prompt, or (None, None).

    The `data:` object queuePrompt sends comes back appended to the prompt as `Context data: {...}`
    and is the reliable path; the `Q<id> <title>: <answer>` text shape is the fallback for a prompt
    queued by hand or by an older page."""
    text = p.get("prompt") or p.get("text") or ""
    m = CTX_RE.search(text)
    if m:
        try:
            d = json.loads(m.group(1))
            if d.get("q"):
                return str(d["q"]), str(d.get("answer", ""))
        except ValueError:
            pass
    m = SHAPE_RE.match(text.split("\n\nContext data:")[0].strip())
    if m:
        qid = m.group("id").lower()
        if not qid.startswith("q"):
            qid = "q" + qid
        qid = "q" + qid.lstrip("q")          # Q5 -> q5, and Qq5 -> q5
        return qid, m.group("answer").strip()
    return None, None


def title_of(qid):
    return next((q.get("title", "") for q in B.load()["questions"] if q.get("id") == qid), "")


def handle(p):
    qid, answer = read_answer(p)
    if qid and answer:
        known = any(q.get("id") == qid for q in B.load()["questions"])
        print(f"ANSWER {qid} | {answer}: {title_of(qid)}".rstrip(": "), flush=True)
        if known:
            B.cmd_answer(type("A", (), {"qid": qid, "answer": answer})())
        else:
            print(f"  (no {qid} on the board; nothing applied)", flush=True)
        return
    text = " ".join((p.get("text") or p.get("prompt") or "").split())[:300]
    sel = p.get("selector") or ""
    if sel:
        print(f"NOTE {sel} | {text}", flush=True)
    else:
        print(f"MESSAGE | {text}", flush=True)


def main():
    a = sys.argv[1:]
    reply = a[a.index("--agent-reply") + 1] if "--agent-reply" in a else None
    once = "--once" in a
    while True:
        out = run_poll(reply)
        reply = None                         # --agent-reply belongs to the first poll only
        prompts, warns, ended = parse(out)
        if warns:
            print(f"LAYOUT {warns} warnings", flush=True)
        for p in prompts:
            handle(p)
        if ended:
            print("SESSION ended", flush=True)
            return
        if once:
            return
        if not prompts and not warns:
            # poll returned with nothing: the server went away. Don't spin.
            print("MESSAGE | poll returned empty; is lavish-axi still up?", flush=True)
            return


if __name__ == "__main__":
    main()
