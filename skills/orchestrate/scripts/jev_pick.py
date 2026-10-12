"""Jev rule picker: ask the Jev decision model which crew-dispatch.json rule fits a task.
`orch.py dispatch --pick "<task text>"` will call cmd_pick() here once I3 applies the orch.py patch
(references/j1-orch-patch-for-i3.md). Until then use `jev_pick.py dispatch --task` below.

Jev answers a multiple-choice question with one choice plus a confidence, never text. Each rule's
`when` text is one option; `default` is the last option. Only the task text goes over the wire:
never quota numbers, never secrets, never file contents.

A rule's optional `jev` field is the text Jev sees for that option (a tighter hint); without it the
`when` text is sent.

  py jev_pick.py pick --task "<task text>" [--rules crew-dispatch.json] [--backend direct|gateway]
      prints: rule=<n|default> confidence=<0-1>   (raw, no gate, no log)
  py jev_pick.py dispatch --task "<task text>"
      what `orch.py dispatch --pick` prints: gated (under 0.6 is ambiguous), 5 s timeout, logged
  py jev_pick.py replay [-n 20] [--short] [--skip LABEL,...]
      replays the last n task rows of state/dispatch-log.jsonl against the seat's rule;
      --short sends only the first line of each Goal

Opt-in: on only when TYPESAFE_API_KEY is set (in the environment, or in ~/.config/jev/.env;
direct, https://api.typesafe.ai) or
AI_GATEWAY_API_KEY plus TYPESAFE_BASE (Vercel gateway, e.g. https://ai-gateway.vercel.sh/typesafe).
Off means no network call. A leading `NAME=` on the key is dropped (the vault stores the whole
line). The key is never printed or logged. Stdlib only.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
RULES_FILE = os.path.join(SKILL, "crew-dispatch.json")
LOG_FILE = os.path.join(SKILL, "state", "dispatch-log.jsonl")
HANDOFFS = os.path.join(os.path.expanduser("~"), ".claude", "handoffs")

BACKENDS = {
    "direct": ("https://api.typesafe.ai/v1/systemone", "jev-latest", "TYPESAFE_API_KEY"),
    "gateway": ("https://ai-gateway.vercel.sh/typesafe/v1/systemone", "typesafe-ai/jev",
                "AI_GATEWAY_API_KEY"),
}
PICK_TIMEOUT = 5          # seconds; a slow Jev is ambiguous, never a stall
CONFIDENCE_GATE = 0.6     # under this the seat decides as today
INPUT_USD_PER_TOKEN = 0.042 / 1_000_000   # output tokens are free
QUESTION = "Which dispatch rule fits this task? Pick `default` when no numbered rule fits."
DEFAULT_WHEN = ("Nothing above clearly fits: applying a list of decisions or answers, config or "
                "settings edits, errands, form chores, bookkeeping, and any other ordinary work. "
                "Choose this when unsure.")


class JevError(Exception):
    pass


def load_rules(path=None):
    with open(path or RULES_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def build_body(table, task, model):
    criteria = {str(i): " ".join(str(r.get("jev") or r.get("when", "")).split())
                for i, r in enumerate(table.get("rules", []), 1)}
    criteria["default"] = DEFAULT_WHEN
    return {"model": model, "state": task,
            "questions": {"rule": {"type": "choice", "instructions": QUESTION,
                                   "criteria": criteria}}}


def clean_key(raw):
    """Vault values can carry a `NAME=` prefix; the API wants the bare key."""
    return re.sub(r"^[A-Z][A-Z0-9_]*=", "", (raw or "").strip()).strip()


KEY_FILE = os.path.join(os.path.expanduser("~"), ".config", "jev", ".env")


def default_env(path=None):
    """The process environment, plus TYPESAFE_API_KEY from ~/.config/jev/.env when the process has
    none (a plain shell never has it; the same ~/.config/<tool>/.env habit as watch and generate)."""
    env = dict(os.environ)
    if not clean_key(env.get("TYPESAFE_API_KEY", "")):
        try:
            with open(path or KEY_FILE, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("TYPESAFE_API_KEY="):
                        env["TYPESAFE_API_KEY"] = line.split("=", 1)[1].strip()
        except OSError:
            pass
    return env


def pick_backend(backend=None, env=None):
    env = default_env() if env is None else env
    if backend:
        return backend
    if env.get("TYPESAFE_API_KEY"):
        return "direct"
    if env.get("AI_GATEWAY_API_KEY"):
        return "gateway"
    raise JevError("no key: set TYPESAFE_API_KEY (or AI_GATEWAY_API_KEY)")


def enabled(env=None):
    """The backend dispatch --pick may use, or None (off, no network call). Stricter than
    pick_backend: the Vercel key alone is not consent, TYPESAFE_BASE must also be set."""
    env = default_env() if env is None else env
    if clean_key(env.get("TYPESAFE_API_KEY", "")):
        return "direct"
    if clean_key(env.get("AI_GATEWAY_API_KEY", "")) and env.get("TYPESAFE_BASE", "").strip():
        return "gateway"
    return None


def _post(url, key, body, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError, ValueError) as e:   # timeout, DNS, TLS, bad JSON
        raise JevError(f"request failed: {type(e).__name__}")


def pick(table, task, backend=None, env=None, timeout=30):
    """(rule, confidence, usage). rule is "1".."N" or "default". Raises JevError on any failure;
    the message never contains the key."""
    env = default_env() if env is None else env
    backend = pick_backend(backend, env)
    url, model, key_var = BACKENDS[backend]
    if env.get("TYPESAFE_BASE", "").strip() and backend == "gateway":
        url = env["TYPESAFE_BASE"].strip().rstrip("/") + "/v1/systemone"
    key = clean_key(env.get(key_var, ""))
    if not key:
        raise JevError(f"no key: set {key_var}")
    status, data = _post(url, key, build_body(table, task, model), timeout)
    if status != 200:
        detail = data.get("detail", data) if isinstance(data, dict) else {}
        kind = detail.get("error_type", "") if isinstance(detail, dict) else ""
        raise JevError(f"{backend} returned HTTP {status} {kind}".strip())
    try:
        ans = data["answers"]["rule"]
        return str(ans["choice"]), float(ans["confidence"]), data.get("usage", {})
    except (KeyError, TypeError, ValueError):
        raise JevError("unexpected response shape")


# --- dispatch --pick ----------------------------------------------------------------------------

def decide(table, task, env=None, timeout=PICK_TIMEOUT, gate=CONFIDENCE_GATE):
    """Gated pick for the seat. Returns a dict: status is clear | ambiguous | off; rule is the
    choice when clear (else the lean when Jev answered, else ""); confidence is None when Jev never
    answered. Off makes no network call. Any failure or timeout is ambiguous, never an exception."""
    env = default_env() if env is None else env
    backend = enabled(env)
    if not backend:
        return {"status": "off", "rule": "", "confidence": None,
                "note": "set TYPESAFE_API_KEY (or AI_GATEWAY_API_KEY plus TYPESAFE_BASE) to enable"}
    try:
        rule, conf, usage = pick(table, task, backend, env, timeout)
    except JevError as e:
        return {"status": "ambiguous", "rule": "", "confidence": None, "note": str(e),
                "backend": backend}
    return {"status": "clear" if conf >= gate else "ambiguous", "rule": rule, "confidence": conf,
            "backend": backend, "tokens": usage.get("input_tokens", 0)}


def log_pick(res, task, caller="", path=None):
    """One row in state/dispatch-log.jsonl with picker: jev. Keeps no task text, only its length
    (the seat's later spawn row carries the rule that was actually used). Never raises."""
    if res["status"] == "off":
        return
    try:
        path = path or os.environ.get("ORCH_DISPATCH_LOG") or LOG_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        row = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "decision": "pick",
               "source": "cli", "caller": caller, "sub": "pick", "picker": "jev",
               "status": res["status"], "task_chars": len(task),
               **{k: res[k] for k in ("rule", "confidence", "backend", "tokens", "note")
                  if res.get(k) not in (None, "")}}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def format_pick(res):
    if res["status"] == "off":
        return f"jev off: {res['note']}. You pick the rule as today."
    if res["status"] == "clear":
        return f"rule={res['rule']} confidence={res['confidence']:.2f}"
    lean = (f" (Jev leaned {res['rule']} at {res['confidence']:.2f})" if res["confidence"] is not None
            else f" ({res.get('note', 'no answer')})")
    return f"ambiguous{lean}. You pick the rule as today."


def cmd_pick(task, rules_path=None, env=None, caller=""):
    """What `orch.py dispatch --pick "<task text>"` runs. Prints one line, logs the pick."""
    res = decide(load_rules(rules_path), task, env)
    log_pick(res, task, caller)
    print(format_pick(res))
    return res


# --- replay -----------------------------------------------------------------------------------

def goal_of(label, handoffs=None, short=False):
    """The ## Goal text of the newest task-<label>-*.md handoff, minus the CHROME RULE boilerplate,
    or None. short keeps only the first line left. This is the only place a log row's task text
    survives (the log keeps no text)."""
    files = sorted(glob.glob(os.path.join(handoffs or HANDOFFS, f"task-{label}-2*.md")))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^## Goal\s*\n(.*?)(?=^## )", text, re.S | re.M)
    if not m:
        return None
    lines = [ln.strip() for ln in m.group(1).splitlines()
             if ln.strip() and not ln.startswith("CHROME RULE")]
    if short:
        lines = lines[:1]
    return " ".join(" ".join(lines).split()) or None


def replay(table, log_path=None, n=20, handoffs=None, backend=None, env=None, short=False,
           skip=()):
    """One result dict per row of the last n spawn rows (pick rows are not spawns). Rows with no
    seat rule (a deny), a label in skip, or no recoverable task text are listed with status
    skipped and no call is made."""
    with open(log_path or LOG_FILE, encoding="utf-8") as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    rows = [r for r in rows if r.get("picker") != "jev"][-n:]
    out = []
    for r in rows:
        res = {"ts": r.get("ts", ""), "sub": r.get("sub", ""), "label": r.get("label", ""),
               "seat": r.get("rule") or "", "jev": "", "conf": None, "status": ""}
        text = None
        if r.get("decision") != "allow" or not res["seat"]:
            res["status"] = "skipped: refused row, no seat rule"
        elif r.get("sub") == "review":
            res["status"] = "skipped: log keeps no review text"
        elif res["label"] in skip:
            res["status"] = "skipped: label on the skip list"
        else:
            text = goal_of(res["label"], handoffs, short)
            if not text:
                res["status"] = "skipped: no handoff text"
        if text:
            try:
                res["jev"], res["conf"], usage = pick(table, text, backend, env)
                res["tokens"] = usage.get("input_tokens", 0)
                res["status"] = "match" if res["jev"] == res["seat"] else "differ"
            except JevError as e:
                res["status"] = f"error: {e}"
        out.append(res)
    return out


def format_replay(results):
    lines = ["ts                   sub     label             seat     jev      conf  result"]
    for r in results:
        conf = "" if r["conf"] is None else f"{r['conf']:.2f}"
        lines.append(f"{r['ts']:<20} {r['sub']:<7} {r['label']:<17} {r['seat']:<8} "
                     f"{r['jev']:<8} {conf:<5} {r['status']}")
    scored = [r for r in results if r["status"] in ("match", "differ")]
    hits = sum(r["status"] == "match" for r in scored)
    tokens = sum(r.get("tokens", 0) for r in scored)
    lines.append(f"scored {len(scored)} of {len(results)} rows, matched {hits}, "
                 f"input tokens {tokens}, about ${tokens * INPUT_USD_PER_TOKEN:.5f}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick")
    p.add_argument("--task", required=True)
    p.add_argument("--rules", default=None)
    p.add_argument("--backend", choices=sorted(BACKENDS))
    r = sub.add_parser("replay")
    r.add_argument("-n", type=int, default=20)
    r.add_argument("--rules", default=None)
    r.add_argument("--log", default=None)
    r.add_argument("--backend", choices=sorted(BACKENDS))
    r.add_argument("--short", action="store_true", help="send only the first line of each Goal")
    r.add_argument("--skip", default="", help="comma list of labels to leave out")
    d = sub.add_parser("dispatch")
    d.add_argument("--task", required=True)
    d.add_argument("--rules", default=None)
    a = ap.parse_args(argv)
    try:
        table = load_rules(a.rules)
        if a.cmd == "pick":
            rule, conf, _ = pick(table, a.task, a.backend)
            print(f"rule={rule} confidence={conf:.2f}")
        elif a.cmd == "dispatch":
            cmd_pick(a.task, a.rules)
        else:
            skip = tuple(s for s in a.skip.split(",") if s)
            print(format_replay(replay(table, a.log, a.n, backend=a.backend, short=a.short,
                                       skip=skip)))
    except (JevError, OSError, ValueError) as e:
        print(f"jev_pick: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
