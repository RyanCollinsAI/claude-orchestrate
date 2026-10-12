"""Fixture tests for scorecard.py.

Every test builds its own tiny world - a `state.json` and a handful of two-line `.jsonl`
transcripts - so nothing here reads Ryan's real board or his real logs, and a test can assert an
exact number instead of "more than zero".
"""

import datetime
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scorecard as S                                                  # noqa: E402

NOW = datetime.datetime(2026, 9, 9, 12, 0, tzinfo=datetime.timezone.utc)


def iso(hours_ago, minutes=0):
    return (NOW - datetime.timedelta(hours=hours_ago, minutes=minutes)).isoformat()


# ---------------------------------------------------------------- fixture writers

def asst(ts, text=None, tool=None, tool_input=None, usage=None, name=None):
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "name": tool, "input": tool_input or {}})
    msg = {"role": "assistant", "model": "claude-opus-5", "content": content}
    if usage:
        msg["usage"] = usage
    return json.dumps({"type": "assistant", "timestamp": ts, "message": msg})


def user(ts, text="ok"):
    return json.dumps({"type": "user", "timestamp": ts,
                       "message": {"role": "user", "content": text}})


def write_log(d, sid, name, lines):
    path = os.path.join(d, sid + ".jsonl")
    head = [json.dumps({"type": "agent-name", "agentName": name, "sessionId": sid})] if name else []
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(head + lines) + "\n")
    return path


def write_state(d, **kw):
    s = {"questions": [], "done": [], "show": [], "sessions": [], "updates": []}
    s.update(kw)
    path = os.path.join(d, "state.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(s, fh)
    return path


BOARD = 'py "C:/x/board.py" '
ORCH = 'py "C:/x/orch.py" '


@pytest.fixture
def world(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    return tmp_path, str(logs)


def build(world, **kw):
    tmp, logs = world
    return S.build(days=7, dirs=[logs], state_path=kw.pop("state", str(tmp / "state.json")),
                   repos=kw.pop("repos", []), now=NOW, **kw)


# ---------------------------------------------------------------- questions

def test_questions_counted_from_commands_and_done_lines(world):
    tmp, logs = world
    write_state(str(tmp), done=[
        {"ts": iso(20), "text": "Q1 should we ship the thing?: A, ship it", "group": ""},
        {"ts": iso(19), "text": "Q2 which crop?: duplicate of q1", "group": ""},
        {"ts": iso(18), "text": "auto-picked q3: B", "group": ""},
    ])
    write_log(logs, "aaaa1111", "orchestrator", [
        asst(iso(30), tool="Bash", tool_input={"command": BOARD + 'add-question --id q1 --title "t"'}),
        asst(iso(29), tool="Bash", tool_input={"command": BOARD + 'add-question --id q2 --title "t"'}),
        asst(iso(28), tool="Bash", tool_input={"command": BOARD + "add-question --id q3 --title \"t\" --auto"}),
        asst(iso(20), tool="Bash", tool_input={"command": BOARD + 'answer q1 "A, ship it"'}),
    ])
    q = build(world)["questions"]
    assert q["asked"] == 3
    assert q["answered_by_human"] == 2          # q1 and q2; q3 was the auto one
    assert q["auto_picked"] == 1
    assert q["withdrawn"] == 1                  # q2's answer says "duplicate"
    assert q["still_open"] == 0
    assert q["wait_median_h"] == 10.0           # q1 asked 30h ago, answered 20h ago


def test_open_question_age_and_unknown_waits(world):
    tmp, logs = world
    write_state(str(tmp),
                questions=[{"id": "q9", "title": "waiting", "created": iso(26), "answered": None}],
                done=[{"ts": iso(5), "text": "Q8 posted by the watcher: B", "group": ""}])
    write_log(logs, "bbbb2222", "orchestrator", [asst(iso(26), text="hi")])
    q = build(world)["questions"]
    assert q["still_open"] == 1
    assert q["oldest_open_id"] == "q9"
    assert q["oldest_open_h"] == 26.0
    # q8 has an answer but no `add-question` command, so its wait cannot be computed and it must
    # not be allowed to drag the median.
    assert q["wait_unknown"] == 1
    assert q["wait_median_h"] is None


def test_question_outside_the_window_is_ignored(world):
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "cccc3333", "orchestrator", [
        asst(iso(24 * 9), tool="Bash", tool_input={"command": BOARD + "add-question --id q1 --title x"}),
        asst(iso(2), tool="Bash", tool_input={"command": BOARD + "add-question --id q2 --title x"}),
    ])
    assert build(world)["questions"]["asked"] == 1


# ---------------------------------------------------------------- decisions

def test_approval_and_a_real_revert(world):
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "dddd4444", "orchestrator", [
        asst(iso(10), tool="SendMessage",
             tool_input={"to": "app-lead [ab12]", "message": "Approved, ship it."}),
        asst(iso(9), tool="SendMessage",
             tool_input={"to": "app-lead [ab12]", "message": "STOP now. Ryan already did it."}),
        asst(iso(8), tool="SendMessage",
             tool_input={"to": "sc-lead [cd34]", "message": "Go ahead with the render."}),
    ])
    d = build(world)["decisions"]
    assert d["messages_sent"] == 3
    assert d["approvals"] == 2
    assert d["reverted_24h"] == 1
    assert d["revert_pct"] == 50.0


def test_then_stop_is_not_a_revert(world):
    """Measured 2026-09-09: "two more jobs, then stop" made 34 of 35 matches false."""
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "eeee5555", "orchestrator", [
        asst(iso(10), tool="SendMessage",
             tool_input={"to": "sc-lead [cd34]", "message": "Approved, go ahead."}),
        asst(iso(9), tool="SendMessage",
             tool_input={"to": "sc-lead [cd34]",
                         "message": "Good. Two more jobs, then stop. Right to stop on the third."}),
    ])
    d = build(world)["decisions"]
    assert d["approvals"] == 1
    assert d["reverted_24h"] == 0


def test_revert_must_be_the_same_pane_and_inside_a_day(world):
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "ffff6666", "orchestrator", [
        asst(iso(40), tool="SendMessage", tool_input={"to": "a [1]", "message": "Approved."}),
        asst(iso(10), tool="SendMessage", tool_input={"to": "a [1]", "message": "Undo that."}),
        asst(iso(9), tool="SendMessage", tool_input={"to": "b [2]", "message": "Approved."}),
        asst(iso(8), tool="SendMessage", tool_input={"to": "c [3]", "message": "Undo that."}),
    ])
    assert build(world)["decisions"]["reverted_24h"] == 0


def test_only_the_orchestrator_seats_messages_count(world):
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "0707aaaa", "app-lead-5-3", [
        asst(iso(5), tool="SendMessage", tool_input={"to": "x [1]", "message": "Approved, ship it."}),
    ])
    assert build(world)["decisions"]["approvals"] == 0


# ---------------------------------------------------------------- panes and time

def test_pane_verbs_and_labels(world):
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "8888aaaa", "orchestrator", [
        asst(iso(6), tool="Bash", tool_input={"command": ORCH + "task app-week-rail --model sonnet"}),
        asst(iso(5), tool="Bash", tool_input={"command": ORCH + "task app-week-rail-2 --model sonnet"}),
        asst(iso(4), tool="Bash", tool_input={"command": ORCH + "kill app-week-rail"}),
        asst(iso(2), tool="Bash", tool_input={"command": ORCH + "spawn --help"}),
    ])
    m = build(world)
    assert m["panes"]["spawned"] == 3           # two tasks plus the --help line's own verb
    assert m["panes"]["killed"] == 1
    # `--help` is not a label, so it can never be mistaken for a respawn.
    assert m["rework"]["respawned_labels"] == 1
    assert m["rework"]["_respawned"] == ["app-week-rail"]


def test_working_versus_waiting_time(world):
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "9999bbbb", "worker", [
        asst(iso(10, 0), text="start"),
        asst(iso(9, 58), text="still going"),    # 2 min  -> working
        user(iso(8, 0)),                         # 118 min -> waiting
    ])
    p = build(world)["panes"]
    assert p["work_h"] == 0.0                    # 2 minutes rounds to 0.0h
    assert p["wait_h"] == pytest.approx(2.0, abs=0.05)
    assert p["wait_pct"] > 90


def test_one_gap_is_capped_at_eight_hours(world):
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "aaaacccc", "parked", [
        asst(iso(100), text="start"),
        asst(iso(20), text="woke up"),           # an 80-hour gap
    ])
    assert build(world)["panes"]["wait_h"] == 8.0


# ---------------------------------------------------------------- rework and tokens

def test_rework_from_the_panes_own_last_words(world):
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "bbbbdddd", "sc-render", [asst(iso(3), text="Had to re-run the render.")])
    write_log(logs, "ccccdddd", "app-fix", [asst(iso(3), text="Done. Merged and clean.")])
    write_log(logs, "ddddeeee", None, [asst(iso(3), text="I redid it.")])
    r = build(world)["rework"]
    assert r["done_rework"] == 2                 # the unlabelled one still counts
    assert r["_rework_names"] == ["sc-render"]   # but never appears as a hex blob
    assert r["done_rework_pct"] == pytest.approx(66.7, abs=0.1)


def test_context_at_retire(world):
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "eeeeffff", "big-pane", [
        asst(iso(4), text="working",
             usage={"input_tokens": 1000, "cache_read_input_tokens": 400000,
                    "cache_creation_input_tokens": 99000}),
    ])
    write_log(logs, "ffffaaaa", "orchestrator", [
        asst(iso(3), tool="Bash", tool_input={"command": ORCH + "kill big-pane"}),
    ])
    t = build(world)["tokens"]
    assert t["retire_events"] == 1
    assert t["retire_ctx_k"] == 500              # (1000 + 400000 + 99000) // 1000
    assert t["peak_ctx_k"] == 500


# ---------------------------------------------------------------- repos, history, render

def test_a_repo_whose_git_is_a_file_is_still_counted(world, tmp_path):
    """The vault's `.git` is a file pointing at a separated git dir; an `isdir` test drops it."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    real = tmp_path / "real.git"
    r = subprocess.run(["git", "init", "--separate-git-dir", str(real), str(repo)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("no git on PATH: " + (r.stderr or "").strip())
    assert not os.path.isdir(str(repo / ".git"))     # it is a file, which is the whole point
    assert S.is_repo(str(repo)) is True


def run(days, end_hours_ago, p90, revert, wait):
    return {"window_days": days,
            "window_end": (NOW - datetime.timedelta(hours=end_hours_ago)).isoformat(),
            "questions": {"wait_p90_h": p90}, "decisions": {"revert_pct": revert},
            "panes": {"wait_pct": wait}}


def test_history_delta_needs_a_matching_window(tmp_path):
    path = str(tmp_path / "h.json")
    a = run(7, 24 * 7, 20.0, 10.0, 50.0)
    S.history_append(a, path)
    runs = S.history_load(path)
    assert S.history_prev(runs, 7, now=NOW) == a
    assert S.history_prev(runs, 1, now=NOW) is None
    b = run(7, 0, 12.0, 15.0, 50.0)
    lines = S.judge(b, prev=a)
    assert "better" in lines[0][3]               # p90 wait fell
    assert "worse" in lines[1][3]                # revert rate rose
    assert "flat" in lines[2][3]


def test_a_snapshot_from_an_hour_ago_is_not_a_weeks_delta(tmp_path):
    """Two runs of the same week an hour apart cover almost the same days. Comparing them prints a
    zero delta that reads as "flat" when nothing was measured at all."""
    path = str(tmp_path / "h.json")
    S.history_append(run(7, 1, 20.0, 10.0, 50.0), path)
    runs = S.history_load(path)
    assert S.history_prev(runs, 7, now=NOW) is None
    S.history_append(run(7, 24 * 4, 30.0, 5.0, 60.0), path)
    prev = S.history_prev(S.history_load(path), 7, now=NOW)
    assert prev is not None and prev["questions"]["wait_p90_h"] == 30.0


def test_render_says_the_watcher_logs_no_agent_state(world):
    tmp, logs = world
    write_state(str(tmp))
    write_log(logs, "1212aaaa", "orchestrator", [asst(iso(2), text="hi")])
    m = build(world)
    for text in (S.render(m), S.render(m, md=True)):
        assert "no agent-state history" in text
    assert S.render(m, md=True).startswith("### Orchestrator scorecard")


def test_scorecard_block_never_raises():
    assert "scorecard unavailable" in S.scorecard_block(days=7, dirs=["/nope"],
                                                        state_path="/nope/x.json",
                                                        repos=[], now="not a datetime")
