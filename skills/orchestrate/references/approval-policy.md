# Approval policy - what to decide yourself, in full

Moved out of SKILL.md section 2 on 2026-09-10 (size budget). Read this when you need the full
criteria or the reasoning behind them, not just the one-line rule.

Approval policy: say yes on your own to small, reversible, in-scope picks - usually the agent's own
recommended option. Bring to your human only money, deletes, anything sent to other people,
credentials and secrets, anything published to YouTube, a school submission, and anything that
changes their plans.

**Decide these yourself (2026-09-09).** Of 62 answered asks (board plus five days of notes), 22
were his and 28 needed a fact only he has; **12 never should have reached him** - he took the
asker's own pick nearly every time. *"if we need a task done, I'd rather it just get done."*

- a bug that gives someone wrong information, or reports success when it failed;
- a free and reversible change - a remote, a config default, no cost and no risk;
- a fix that only removes false positives or negatives, the other option plainly worse;
- a feature plugging a gap the product already flags, with no design tradeoff;
- a write-up that already names each alternative and why it lost - that rejection IS the decision;
- a preference he has already stated, satisfied by a measurement - do not re-ask per recurrence;
- one obviously safe default with no trigger attached.

**Check what you already know before asking.** Four of the twelve were askable only because nobody
looked - a class already dropped, a config default already set to the answer, a fact already in
memory (no Mac, so that route was dead). "The answer cannot differ" is not a question. Mark a real
one `--auto` on `board.py add-question` so it picks itself in 24h; if you are asking to be told you
are right, act and post a Done line.

Deciding is not answering **for** him: an answer you write reads on the board exactly like his, so
`board.py answer` now needs `--by <session> --reason "<why>"` (G-7 refuses it without them) and is
only for withdrawing or superseding.

**Verifiable approvals (2026-10-02).** A pane cannot verify a quoted "Ryan said yes", so it refuses one (school-mate did, for a BA 323 submit). When Ryan tells you to approve a submit or pay, mint a record instead of relaying words:

- `orch.py approve <pane> --action "<one exact action>" --words "<Ryan's exact words>"` - seat only, a peer is refused. Writes `board/approvals.json`: single use, 30 min, one target, one action.
- `orch.py prompt <pane> --approval <id> "<text>"` appends the id and the check command to the message.
- The pane runs `orch.py approval check <id> --as <pane> --action "<exact action>"` and acts only on VALID. A wrong pane, a different action, an expired or used id all fail. `orch.py approval ls` lists live ones.
- Code is `scripts/approvals.py`. Limit: it is a file, so it stops a pane being talked into acting, not a pane with a shell that hand-edits the JSON.

A session started before this policy may refuse a relayed approval ("a peer message does not stand
in for their answer"). When that happens and you already have the decision, do the action yourself -
edit the file, run the command - instead of bouncing it back.

**Self-approval requires proof (2026-09-13).** Revert rate on approvals the seat gave itself hit
16.9% (+3.0 vs the prior week) - worse, not better. A self-approval (yours or `auto_accept.py`
pressing Enter for a pane) is only valid when the pane's own report carries verify evidence:

- a test count ("14 tests passed", "3 specs failed");
- a proof-screenshot or render path;
- an explicit "verified on `<surface>`" line.

No evidence, no self-approval - ask instead, even for a pick that would otherwise qualify above.
`auto_accept.py` enforces this in code (`has_verify_evidence`, `AUTO_ACCEPT_REQUIRE_VERIFY=0` to
disable) and logs every decision, accepted or refused, to `board/self-approvals.jsonl` with an id
so a later revert can be recorded against the approval it undoes instead of just against a date.
