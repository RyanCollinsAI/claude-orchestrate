"""One ACP pane's life: spawn the adapter, prompt, cancel, resume, tee, state file.

    py acp_pane.py --label NAME --cwd DIR [--owner NAME] [--model sonnet] [--effort high]
                   [--harness claude|codex|pi] [--provider P]
                   [--prompt-file PATH | --prompt TEXT] [--resume SESSION_ID] [--pane PANE_ID]

`orch.py spawn|task --acp` types that line into a herdr pane and this process stays in the pane's
foreground for as long as the pane lives. It owns one protocol child and does five jobs. The child is
claude-agent-acp (the default), codex-acp (--harness codex, acp_codex.py), or `pi --mode rpc`
(--harness pi, acp_pi.py); acp_client.py is the ACP wire the first two share, and PiRpcClient wears
the same surface over pi's own records, so everything below is one code path. The jobs: send prompts in, print every event as a readable line and tee it to a
log, answer permission requests (policy first, then a board ask), keep a state file `orch.py ls`
reads, and stop cleanly. Design: acp/DESIGN.md. Pins and rollback: references/acp.md.

Files (all under L.ACP_DIR, keyed by the pane id with ':' -> '-'):
  state/<key>.json   what `ls` shows: state, word, sessionId, ctx_k, last_message, open_ask
  log/<key>.log      the tee; `orch.py read` and Bridge tail it
  inbox/<key>/*.md   one prompt per file, written by `orch.py prompt` (write .tmp, rename)
  inbox/<key>/*.cancel   the Escape key: cancel the open turn
  inbox/<key>/stop       finish the prompts already queued, then shut down (Ctrl+C stops at once)

Exit: 0 clean stop, 2 start failed (the reason is in the state file and the tee), 3 adapter died.
"""
import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import acp_client as C
import acp_codex as CODEX
import acp_pi as PI
import orchlib as L

ANSWER_RE = re.compile(r"\[answer to (q-[0-9a-f]+)\]")
ALLOW_PICK = "Allow once"
HEARTBEAT_S = 15


class Tee:
    """Print a line and append it to the log. The pane's screen and the file stay identical."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)

    def line(self, text):
        text = text.rstrip("\n")
        with self.lock:
            try:
                print(text, flush=True)
            except (OSError, ValueError):
                pass
            if self.path:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%H:%M:%S')} {text}\n")


class AcpPane:
    def __init__(self, label, cwd, key, owner="", model="sonnet", effort="", pane="", policy=None,
                 ask_timeout=None, adapter_cmd=None, resume="", env=None, asks_mod=None,
                 poll=0.4, ask_poll=1.0, harness="claude", provider="", report=""):
        self.label, self.cwd, self.key, self.owner = label, os.path.abspath(cwd), key, owner
        self.model, self.effort, self.pane, self.resume = model, effort, pane, resume
        self.harness, self.provider, self.session_file = harness, provider, ""
        self.report = report
        self.policy = policy if policy is not None else C.load_policy(L.ACP_POLICY)
        self.ask_timeout = ask_timeout if ask_timeout is not None else self.policy.get("ask_timeout_s", 1800)
        self.adapter_cmd, self.env = adapter_cmd, env
        self.asks = asks_mod
        self.poll, self.ask_poll = poll, ask_poll
        self.tee = Tee(L.acp_path("log", key, ".log"))
        self.inbox = os.path.join(L.ACP_DIR, "inbox", key)
        self.client, self.sid = None, ""
        self.q = queue.Queue()
        self.stop_event, self.cancel_event = threading.Event(), threading.Event()
        self.turn_open = threading.Event()
        self.pending, self.draining = 0, False       # prompts submitted but not finished; stop-file mode
        self.pending_lock = threading.Lock()
        self.asked = set()
        self.permissions = []                 # (title, action, rule) for the audit and the tests
        self._buf, self._turn_text = "", []
        self._buf_lock = threading.Lock()
        self._replay, self._stale_ask = False, ""
        self.exit_code = 0

    # ------------------------------------------------------------ state
    def put(self, **fields):
        base = dict(label=self.label, owner=self.owner, pane=self.pane, pid=os.getpid(), cwd=self.cwd,
                    model=self.model, sessionId=self.sid, harness=self.harness)
        if self.session_file:
            base["sessionFile"] = self.session_file
        if self.client and self.client.proc:
            base["adapter_pid"] = self.client.pid
        return L.acp_state_put(self.key, **{**base, **fields})

    def report_herdr(self, state):
        """Optional: tell herdr's sidebar. Off unless ACP_HERDR_REPORT=1 - the child's own herdr
        integration marks the pane already (probe 3), and two reporters could fight."""
        if os.environ.get("ACP_HERDR_REPORT") != "1" or not self.pane:
            return
        st = {"working": "working", "idle": "idle", "question": "blocked"}.get(state)
        if st:
            subprocess.run(["herdr", "pane", "report-agent", self.pane, "--source", "orch:acp",
                            "--agent", self.label, "--state", st], capture_output=True, timeout=15)

    def fail(self, why, code=2):
        self.tee.line(f"ERROR {why}")
        self.put(state="error", word="ERROR", error=why)
        self.exit_code = code
        return False

    # ------------------------------------------------------------ start
    def start(self):
        self.tee.line(f"acp pane {self.label}: cwd={self.cwd} model={self.model}"
                      + (f" resume={self.resume}" if self.resume else ""))
        self.put(state="starting", word="", started=time.strftime("%Y-%m-%dT%H:%M:%S"))
        key_vars = {"claude": C.API_KEY_VARS, "codex": CODEX.API_KEY_VARS, "pi": ()}[self.harness]
        for var in key_vars:
            if os.environ.get(var):
                login = "ChatGPT" if self.harness == "codex" else "Claude"
                return self.fail(f"refusing to start: {var} is set. Decision D2: {login} subscription "
                                 f"login only, no API key, ever. Unset it and spawn again.")
        cmd = self.adapter_cmd
        if cmd is None:
            cmd = self.harness_cmd()
            if not cmd:
                return False
        wire = L.acp_path("log", self.key, ".wire.log") if os.environ.get("ACP_WIRE_LOG") == "1" else None
        log_dir = os.path.dirname(L.acp_path("log", self.key, ""))
        env = self.env
        if env is None:
            env = {"claude": lambda: C.child_env(log_dir=log_dir),
                   "codex": lambda: CODEX.child_env(log_dir=log_dir),
                   "pi": PI.child_env}[self.harness]()
        kw = dict(stderr_path=L.acp_path("log", self.key, ".stderr.log"), on_notification=self.on_notify,
                  on_request=self.on_request, wire_log=wire)
        self.client = (PI.PiRpcClient(cmd, self.cwd, env, policy=self.policy, **kw) if self.harness == "pi"
                       else C.AcpClient(cmd, self.cwd, env, **kw))
        try:
            self.client.start()
            self.client.initialize()
            auth = C.require_subscription(self.client.wait_auth(10), env)
            self.tee.line(f"auth: {auth.get('kind')}/{auth.get('label')}")
            self._open_session()
        except C.AuthRefused as e:
            self.client.close(grace=5)
            return self.fail(str(e))
        except (C.AcpError, C.AcpClosed, TimeoutError, OSError) as e:
            self.client.close(grace=5)
            return self.fail(f"could not start the session: {type(e).__name__}: {e}")
        self.put(state="idle", word="", sessionId=self.sid)
        mode = C.harness_policy(self.policy, self.harness).get("mode") or "no modes"
        self.tee.line(f"session {self.sid} ready ({self.harness}, {mode})")
        self.report_herdr("idle")
        return True

    def harness_cmd(self):
        """argv of the protocol child for this harness, or a falsy value after fail() says why."""
        if self.harness == "pi":
            cmd = PI.pi_command(PI.pi_args(self.model, self.provider, self.effort, self.label))
            return cmd or self.fail("pi is not on PATH: cannot start `pi --mode rpc`")
        adapter = CODEX.adapter_path() if self.harness == "codex" else C.adapter_path()
        node = C.node_exe()
        if not adapter:
            where = "skills/orchestrate/acp/codex" if self.harness == "codex" else "skills/orchestrate/acp"
            name = "codex-acp" if self.harness == "codex" else "claude-agent-acp"
            return self.fail(f"{name} is not installed: run `npm ci --omit=optional` in {where} "
                             f"(see references/acp.md)")
        if C.node_major(node) < C.NODE_MIN:
            return self.fail(f"node >= {C.NODE_MIN} is required (found major {C.node_major(node)})")
        return [node, adapter]

    def _session_call(self, fn, *args):
        """A session call. codex-acp may answer "authentication required" before the first session
        even though the login is there; the auth check above already proved it is the ChatGPT account,
        and `authenticate chat-gpt` on a ChatGPT account returns at once without opening a browser."""
        try:
            return fn(*args)
        except C.AcpError as e:
            if self.harness != "codex" or not CODEX.is_auth_error(e):
                raise
            self.tee.line("codex asked for authentication; confirming the ChatGPT login (no browser)")
            self.client.authenticate(CODEX.CHATGPT_METHOD)
            return fn(*args)

    def _open_session(self):
        # claude: the pane label becomes `claude --name`; the other two take no such field. Pi's
        # name rides on its --name flag and on set_session_name.
        name = self.label if self.harness in ("claude", "pi") else ""
        if self.resume:
            self._replay = True
            try:
                res = self._session_call(self.client.load_session, self.resume, self.cwd)
            finally:
                self._replay = False
            self.sid = res.get("sessionId") or self.resume
        else:
            res = self._session_call(self.client.new_session, self.cwd, name)
            self.sid = res["sessionId"]
        self.session_file = res.get("sessionFile", "")
        if self.harness == "codex":
            CODEX.configure(self.client, self.sid, res, self.model, self.effort, self.policy, self.tee.line)
            return
        # bypassPermissions never reaches the permission callback; acceptEdits asks for everything
        # that is not a cwd edit, which is what the policy decides (DESIGN 3.1). Pi has no modes.
        mode = C.harness_policy(self.policy, self.harness).get("mode")
        if mode:
            self.client.set_mode(self.sid, mode)
        opts = res.get("configOptions") or []
        bare = self.model.split("/")[-1]
        for cid, wanted in (("model", [self.model, bare, L.MODELS.get(self.model, "")]),
                            ("effort", [self.effort])):
            if not any(wanted):
                continue
            val = C.pick_config_value(opts, cid, wanted)
            if val:
                self.client.set_config_option(self.sid, cid, val)
            else:
                self.tee.line(f"note: {cid} {wanted[0]!r} is not offered by this session; left as is")

    # ------------------------------------------------------------ events from the agent
    def flush_message(self, final=False):
        with self._buf_lock:
            *lines, self._buf = self._buf.split("\n")
            if final and self._buf:
                lines.append(self._buf)
                self._buf = ""
        for ln in lines:
            if ln.strip():
                self.tee.line(f"{self.harness}: {ln}")

    def on_notify(self, method, params):
        if method != "session/update" or self._replay:
            return
        u = params.get("update") or {}
        kind = u.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = (u.get("content") or {}).get("text", "")
            with self._buf_lock:
                self._buf += text
                self._turn_text.append(text)
            self.flush_message()
        elif kind == "tool_call":
            self.flush_message(final=True)
            cmd = (u.get("rawInput") or {}).get("command")
            self.tee.line(f"tool: {C.short(u.get('title') or u.get('name') or '?')}"
                          + (f": {C.short(cmd, 120)}" if isinstance(cmd, str) and cmd else ""))
        elif kind == "tool_call_update":
            status = u.get("status")
            txt = " ".join((c.get("content") or {}).get("text", "") for c in (u.get("content") or [])
                           if isinstance(c, dict) and c.get("type") == "content")
            if status in ("completed", "failed"):
                self.tee.line(f"tool {status}: {C.short(txt)}")
        elif kind == "usage_update":
            self.put(ctx_k=int((u.get("used") or 0) / 1000), ctx_size_k=int((u.get("size") or 0) / 1000),
                     model_id=(u.get("_meta") or {}).get("_claude/model", ""))
        elif kind == "current_mode_update":
            self.tee.line(f"mode is now {u.get('currentModeId')}")
        elif kind == "agent_notice":
            self.flush_message(final=True)
            self.tee.line(f"{self.harness} notice: {C.short(u.get('text', ''))}")

    # ------------------------------------------------------------ permission requests
    def on_request(self, method, params):
        if method != "session/request_permission":
            raise C.AcpError(-32601, f"client does not serve {method}")
        call = params.get("toolCall") or {}
        title = call.get("title") or call.get("name") or "?"
        d = C.decide(call, self.cwd, self.policy)
        action = self.ask_board(call, title, d) if d.action == "ask" else d.action
        oid = C.pick_option(params.get("options"), action) if action else None
        self.permissions.append((title, action or "cancelled", d.rule or d.reason))
        self.tee.line(f"PERMISSION {C.short(title, 120)} -> {action or 'cancelled'} ({d.reason})")
        return C.outcome(oid)

    def _ask_row(self, aid):
        return next((r for r in self.asks._load() if r.get("id") == aid), None)

    def ask_board(self, call, title, d):
        """Put the question on the board and wait for Ryan's pick. -> "allow", "reject", or None
        (cancelled). Never waits past ask_timeout: an unanswered request holds the turn open for
        ever, so on timeout the answer is a rejection and the turn goes on."""
        if self.asks is None:
            import asks as _asks
            self.asks = _asks
        q = f"{self.label} asks to run: {C.short(title, 300)} ({d.reason})"
        rec = self.asks.add(q, [ALLOW_PICK, "Reject"], action=C.short(title, 300))
        self.asked.add(rec["id"])
        self.put(state="question", word="QUESTION", open_ask=rec["id"], question=q)
        self.report_herdr("question")
        self.tee.line(f"ASK {rec['id']} on the board: {q}")
        deadline = time.time() + self.ask_timeout
        while True:
            if self.stop_event.is_set() or self.cancel_event.is_set():
                return None
            row = self._ask_row(rec["id"])
            if row and row.get("status") == "answered":
                self.put(state="working", word="", open_ask="")
                self.report_herdr("working")
                return "allow" if row.get("answer") == ALLOW_PICK else "reject"
            if time.time() > deadline:
                self.tee.line(f"ASK {rec['id']} had no answer in {int(self.ask_timeout)}s; rejecting "
                              f"this one. Ask again if it is still needed.")
                self._stale_ask = rec["id"]
                self.put(state="working", word="QUESTION", open_ask=rec["id"], ask_timed_out=True)
                return "reject"
            time.sleep(self.ask_poll)

    # ------------------------------------------------------------ turns
    def submit(self, text):
        with self.pending_lock:
            self.pending += 1
        self.q.put(text)

    def run_turn(self, text):
        self.cancel_event.clear()
        self._turn_text, self._stale_ask = [], ""
        self.put(state="working", word="", turn_started=time.time(), open_ask="", ask_timed_out=False)
        self.report_herdr("working")
        self.tee.line(f"prompt: {C.short(text, 200)}")
        self.turn_open.set()
        stop = ""
        try:
            stop = self.client.prompt(self.sid, text)
        except C.AcpClosed:
            self.flush_message(final=True)
            if not self.stop_event.is_set():
                self.fail("the adapter exited during a turn", code=3)
                self.tell_owner("ERROR", "the adapter exited during a turn")
            self.stop_event.set()
            return
        except C.AcpError as e:
            # auth_required, a session limit, a crashed child: the case dead_mark() scraped
            self.flush_message(final=True)
            self.tee.line(f"ERROR {e.message}")
            self.put(state="error", word="ERROR", error=e.message, last_message=e.message)
            self.tell_owner("ERROR", e.message)
            return
        finally:
            self.turn_open.clear()
        self.flush_message(final=True)
        last = "".join(self._turn_text).strip()
        # An ask nobody answered in time stays QUESTION until the next turn, so `ls` and the
        # watchdog still show that a pane is waiting on a human decision (DESIGN 3.3).
        word = "QUESTION" if self._stale_ask else "" if stop == "cancelled" else L._classify_text(last)[0]
        self.put(state="idle", word=word, last_message=last[-2000:], stop_reason=stop,
                 open_ask=self._stale_ask, ask_timed_out=bool(self._stale_ask))
        self.report_herdr("idle")
        self.tee.line(f"turn ended: {stop}")
        if word:
            self.tell_owner(word, last)

    def tell_owner(self, word, last):
        """codex and pi have no SendMessage, and the watchers do not read an ACP pane's screen, so
        nothing else would tell the owner that a turn ended. Say it through `orch.py prompt`, the
        same door every other message to a session uses. A claude child has SendMessage itself."""
        if self.harness == "claude" or not self.owner:
            return
        msg = (f"{self.label} turn ended {word}" + (f"; report file {self.report}" if self.report else "")
               + (f": {C.short(last, 300)}" if last else ""))
        try:
            subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "orch.py"),
                            "prompt", self.owner, msg], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            self.tee.line(f"note: could not tell {self.owner} that the turn ended")

    def worker(self):
        while not self.stop_event.is_set():
            try:
                text = self.q.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                self.run_turn(text)
            finally:
                with self.pending_lock:
                    self.pending -= 1

    def cancel(self):
        if self.turn_open.is_set() and self.client and self.sid:
            self.tee.line("cancel requested")
            self.cancel_event.set()
            try:
                self.client.cancel(self.sid)
            except C.AcpClosed:
                pass

    def stop(self):
        self.stop_event.set()

    # ------------------------------------------------------------ inbox
    def deliver(self, text):
        m = ANSWER_RE.search(text)
        if m and m.group(1) in self.asked:
            # The board's reply to our own ask. The ask loop reads asks.json; the model needs none of it.
            self.tee.line(f"ask {m.group(1)} answered on the board")
            return
        self.submit(text)

    def poll_inbox(self):
        try:
            names = sorted(os.listdir(self.inbox))
        except OSError:
            return
        for name in names:
            path = os.path.join(self.inbox, name)
            if name.endswith(".tmp"):
                continue
            if name == "stop" or name.endswith(".stop"):
                os.remove(path)
                self.draining = True         # finish every prompt already queued, then stop
            elif name.endswith(".cancel"):
                os.remove(path)
                self.cancel()
            elif name.endswith(".md"):
                try:
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                    os.remove(path)
                except OSError:
                    continue
                self.deliver(text)

    # ------------------------------------------------------------ the loop
    def run(self, first_prompt=""):
        if not self.start():
            return self.exit_code
        os.makedirs(self.inbox, exist_ok=True)
        threading.Thread(target=self.worker, daemon=True, name="acp-turns").start()
        if first_prompt:
            self.submit(first_prompt)
        last_beat = time.time()
        try:
            while not self.stop_event.is_set():
                self.poll_inbox()
                if self.draining and self.pending == 0:
                    break
                if self.client.closed and not self.stop_event.is_set():
                    self.fail("the adapter exited", code=3)
                    break
                if time.time() - last_beat > HEARTBEAT_S:
                    self.put()
                    last_beat = time.time()
                time.sleep(self.poll)
        except KeyboardInterrupt:
            self.tee.line("interrupted; stopping")
        self.shutdown()
        return self.exit_code

    def forget_registry_row(self):
        """The child claude is stopped by closing the adapter, not by a clean /exit, so its row in
        ~/.claude/sessions stays behind and `ls` would list a ghost. Remove ours once its process is gone."""
        if not self.sid:
            return
        for f in (os.path.join(L.SESS, n) for n in os.listdir(L.SESS) if n.endswith(".json")) \
                if os.path.isdir(L.SESS) else []:
            try:
                with open(f, encoding="utf-8") as fh:
                    row = json.load(fh)
                if row.get("sessionId") == self.sid and not L.pid_alive(row.get("pid")):
                    os.remove(f)
            except (OSError, ValueError):
                continue

    def shutdown(self):
        self.stop_event.set()
        if self.turn_open.is_set():
            self.cancel()
            deadline = time.time() + 10
            while self.turn_open.is_set() and time.time() < deadline:
                time.sleep(0.1)
        if self.client:
            self.client.close()
        self.forget_registry_row()
        if not self.exit_code:
            self.put(state="stopped", word="")
        self.tee.line("stopped")


def main(argv=None):
    ap = argparse.ArgumentParser(description="one ACP pane (see the module docstring)")
    ap.add_argument("--label", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--owner", default="")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--effort", default="")
    ap.add_argument("--harness", choices=("claude", "codex", "pi"), default="claude")
    ap.add_argument("--provider", default="", help="pi only: --provider for pi")
    ap.add_argument("--report", default="", help="codex and pi: the report file named in the prompt")
    ap.add_argument("--pane", default=os.environ.get("HERDR_PANE_ID", ""))
    ap.add_argument("--prompt-file", default="")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--resume", default="")
    ap.add_argument("--ask-timeout", type=float, default=None)
    ap.add_argument("--key", default="")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):      # a console code page must never drop a line of the tee
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    key = a.key or L.acp_key(a.pane or a.label)
    first = a.prompt
    if a.prompt_file:
        with open(a.prompt_file, encoding="utf-8") as fh:
            first = fh.read()
    pane = AcpPane(a.label, a.cwd, key, a.owner, a.model, a.effort, a.pane, resume=a.resume,
                   ask_timeout=a.ask_timeout, harness=a.harness, provider=a.provider,
                   report=a.report)
    return pane.run(first)


if __name__ == "__main__":
    sys.exit(main())
