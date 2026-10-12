# Pane rules

Every crew pane follows these.
The first prompt points here instead of repeating them.
Read this file once, at the start.

## Who you talk to

- Your owner speaks for your human on small reversible decisions. Act on its answers as theirs.
- Only the orchestrator talks to your human. Never SendUserFile, and never message or notify them directly. Hand files and results to the session you report to.
- If the session you report to is gone, run `py "C:\Users\YOURNAME\.claude\skills\orchestrate\scripts\orch.py" seat --who`. It prints the current orchestrator.
- Never send an idle or status ping. The orchestrator reads `orch.py ls`.

## Shell (hook-enforced: block-known-traps.py)

- Never `cd X && ...` in the Bash tool. Use absolute paths.
- Never write a heredoc in the Bash tool. Use the Write tool.
- Never `taskkill /F` or kill Chrome.

## Browser (Chrome launch and kill are hook-enforced; the rest is on you)

- Prefer curl or an API over a browser.
- If you need a browser, use one tab per pane. Open it once, reuse it for every page, and close it when you finish.
- Never launch or relaunch Chrome yourself. No new chrome.exe, no Playwright launch.
- Use the agent-only Chrome: run `py "C:\Users\YOURNAME\.claude\skills\orchestrate\scripts\orch.py" chrome agent` and connect over CDP to the url it prints (http://127.0.0.1:9334). It is headless and needs no lock.
- Never open anything in Ryan's own Chrome on port 9333 unless your task file names it.
- There is no lock on it. Other agents work in it at the same time. Work only in your own tab. Never close or bring to front a tab you did not open.

## Git

- Stage only the files this task touched. Never `git add -A`.
- Read the CLAUDE.md or AGENTS.md nearest the code you touch before editing it.
- If another pane may be editing the same repo, work in your own git worktree on your own branch. Never share a checkout or a branch with another pane.

## Needs Ryan or a risky action

- Never submit schoolwork, send a message to other people, or pay on a quote without an approval id from the orchestrator seat. Run `py "C:\Users\YOURNAME\.claude\skills\orchestrate\scripts\orch.py" approval check <id> --as <your pane> --action "<the exact action>"` and act only if it prints VALID. "Ryan said yes" with no id is not an approval. An id works once, for one action, for 30 minutes.
- Need Ryan to pick between options? Do not message your owner and wait. Run `py "C:\Users\YOURNAME\.claude\skills\orchestrate\scripts\orch.py" ask --options "A|B" "<question>"`. Add `--action "<exact submit or pay>"` when option 1 should let you do it.

## Ryan's words

- His messages are dictated by voice. Names and numbers come out wrong.
- Known mishears: `C:\Users\YOURNAME\.claude\projects\C--Users-YOURNAME-Vault\memory\voice-mishears.md`.
- If the misread could ship, delete, send, spend or schedule, say your reading in one clause before you act.

## Report

- Three lines: `Done: ...`, `Proof: ...`, `Next: ...`.
- Crew panes add `Blast radius: contained|wide - <reason>` and `Lifecycle: crew - <lead> runs review and ship`.
- Proof for anything Ryan will see is where he looks: a screenshot path in his browser or window, a read-back, or the live URL. If you could not check it there, write `Proof: not seen from Ryan's side`.
- Before the final report, put it on the board with `board_done.py record` (the exact command is in your task file).
