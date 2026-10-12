"""Pane questions that Ryan answers once, in the orchestrator seat.

Why this exists (2026-10-02): a pane with an A/B question asked its owner, the owner relayed it to
Ryan, Ryan answered, the owner relayed that back. Three hops, and the answer was often stale. Now
the pane writes the question to a file, the seat shows it above the prompt with one button per
option (plugins/ask-board), and Ryan's pick goes straight back to the asking pane.

    orch.py ask [--options "Yes|No"] [--action "<exact action>"] [--to <name>] <question>
    orch.py asks [--json] [--all]
    orch.py answer <id> <option number or text>

- `ask` is run by the pane that has the question. It prints an id and returns at once. The answer
  arrives later as a normal `[from <seat>]` prompt in that pane.
- `--action` marks a submit or pay. The FIRST option is the go-ahead. When Ryan picks it, `answer`
  mints a single-use approval (approvals.py) and appends it to the reply. Any other pick sends the
  answer with no approval, so the pane cannot act.
- `answer` is refused outside the orchestrator seat.

Data: board/asks.json, a list of rows. Answered rows stay a day for the audit trail.
"""
import json
import os
import secrets
import time

import orchlib as L

ASKS_FILE = os.path.join(L.BOARD_DIR, "asks.json")
KEEP_SECONDS = 24 * 3600
OPEN_SECONDS = 24 * 3600          # an ask nobody answered in a day is stale, not open
_LOCK_STALE = 10


def _load():
    for i in range(8):
        try:
            with open(ASKS_FILE, encoding="utf-8") as fh:
                d = json.load(fh)
            return d if isinstance(d, list) else []
        except PermissionError:
            # A writer's os.replace has the file for an instant. Reading [] here would make add()
            # save only its own row and drop every other open ask.
            if i < 7:
                time.sleep(0.01 * (i + 1))
        except (OSError, ValueError):
            return []
    return []


def _save(rows):
    os.makedirs(os.path.dirname(ASKS_FILE), exist_ok=True)
    tmp = ASKS_FILE + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1)
    L.replace_retry(tmp, ASKS_FILE)


class _Lock:
    def __enter__(self):
        self.path = ASKS_FILE + ".lock"
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        end = time.time() + 5
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > _LOCK_STALE:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                if time.time() > end:
                    raise SystemExit("asks file is locked; try again")
                time.sleep(0.05)

    def __exit__(self, *exc):
        os.close(self.fd)
        try:
            os.remove(self.path)
        except OSError:
            pass


def _opt(a, flag, default=None):
    if flag in a:
        i = a.index(flag)
        if i + 1 < len(a):
            return a[i + 1]
    return default


def _positional(a):
    """Words that are not a flag or a flag's value."""
    out, skip = [], False
    for w in a:
        if skip:
            skip = False
        elif w in ("--options", "--action", "--to"):
            skip = True
        elif not w.startswith("--"):
            out.append(w)
    return out


def add(question, options, action="", to="", now=None):
    now = now if now is not None else time.time()
    options = [o.strip() for o in options if o.strip()]
    if not question.strip():
        raise SystemExit('usage: orch.py ask [--options "A|B"] [--action "<exact action>"] <question>')
    if len(options) < 2:
        raise SystemExit("ask needs at least 2 options, split with | (example: --options \"A|B\")")
    rec = {"id": "q-" + secrets.token_hex(3), "ts": now, "status": "open",
           "from": L.caller_name(), "from_pane": os.environ.get("HERDR_PANE_ID", ""),
           "to": to or "orchestrator", "question": question.strip(), "options": options,
           "action": action.strip(), "answer": None, "answered_ts": None}
    with _Lock():
        rows = [r for r in _load() if now - r.get("ts", 0) < KEEP_SECONDS]
        rows.append(rec)
        _save(rows)
    return rec


def open_rows(now=None):
    now = now if now is not None else time.time()
    return [r for r in _load() if r.get("status") == "open" and now - r.get("ts", 0) < OPEN_SECONDS]


def pick(rec, choice):
    """Return the option text for a 1-based number or a case-insensitive option text."""
    choice = (choice or "").strip()
    opts = rec["options"]
    if choice.isdigit() and 1 <= int(choice) <= len(opts):
        return opts[int(choice) - 1]
    for o in opts:
        if o.casefold() == choice.casefold():
            return o
    raise SystemExit(f"{choice!r} is not one of the options: " + " | ".join(opts))


def answer(aid, choice, send, now=None):
    """Record Ryan's pick and deliver it to the asking pane with `send(name, text)`."""
    if not L.caller_is_seat():
        raise SystemExit("answer REFUSED: only the orchestrator seat answers asks "
                         "(`py orch.py seat` shows who is).")
    now = now if now is not None else time.time()
    with _Lock():
        rows = _load()
        rec = next((r for r in rows if r.get("id") == aid), None)
        if not rec:
            raise SystemExit(f"no ask with id {aid!r}")
        if rec["status"] != "open":
            raise SystemExit(f"{aid} is already answered: {rec['answer']!r}")
        opt = pick(rec, choice)
        rec["status"], rec["answer"], rec["answered_ts"] = "answered", opt, now
        _save(rows)
    text = f'[answer to {aid}] Ryan picked "{opt}" for: {rec["question"]}'
    if rec["action"] and opt == rec["options"][0]:
        import approvals
        ap = approvals.mint(rec["from"], rec["action"], f'picked "{opt}" on ask {aid}', now)
        text += approvals.approval_suffix(ap["id"])
    elif rec["action"]:
        text += " Do NOT do the action. You have no approval."
    send(rec["from"], text)
    return rec, opt


def cmd_ask(a):
    action = _opt(a, "--action", "")
    options = (_opt(a, "--options") or "Yes|No").split("|")
    rec = add(" ".join(_positional(a[1:])), options, action, _opt(a, "--to", ""))
    print(f"ask {rec['id']} is open for the orchestrator seat: {rec['question']}")
    print("  options: " + " | ".join(f"{i}. {o}" for i, o in enumerate(rec["options"], 1)))
    print("  Do not wait in a loop. Ryan's pick arrives in this pane as a prompt.")
    if action:
        print(f"  Pick 1 sends an approval id for: {action}. Check it before you act.")


def cmd_asks(a):
    rows = _load() if "--all" in a else open_rows()
    if "--json" in a:
        print(json.dumps({"seat": L.caller_is_seat(), "asks": rows}))
        return
    for r in rows:
        mins = int((time.time() - r["ts"]) / 60)
        got = f" -> {r['answer']}" if r["status"] == "answered" else ""
        print(f"{r['id']}  {r['from']}  {mins}m ago  {r['question']}  "
              f"[{' | '.join(r['options'])}]{got}")
    if not rows:
        print("no open asks")


def main(a, send):
    cmd = a[0]
    if cmd == "ask":
        cmd_ask(a)
    elif cmd == "asks":
        cmd_asks(a)
    else:
        if len(a) < 3:
            raise SystemExit("usage: orch.py answer <id> <option number or text>")
        _rec, opt = answer(a[1], " ".join(a[2:]), send)
        print(f"answered {a[1]}: {opt}")
