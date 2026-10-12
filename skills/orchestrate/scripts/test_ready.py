import json

import ready

BACKLOG = """# Menu

| ID | Feature | Status |
|----|---------|--------|
| T-001 | ready item | new |
| T-002 | blocked item, depends on T-003 | new |
| T-003 | unfinished blocker | picked |
| T-004 | held item | new |
| T-005 | finished item | shipped |
| T-006 | blocked-by: T-005 (finished) so it is ready | new |
| T-007 | dangling blocker, depends on T-999 | new |
| T-008 | no status cell at all |
| T-009 | expired hold | new |
| T-010 | status in bold | **DONE** - merged |
| prose line that is not a row | new |
"""


def _setup(tmp_path, holds=None, state=None):
    backlog, board = tmp_path / "_backlog", tmp_path / "board"
    backlog.mkdir()
    board.mkdir()
    (backlog / "menu.md").write_text(BACKLOG, encoding="utf-8")
    (board / "state.json").write_text(json.dumps(state or {}), encoding="utf-8")
    (board / "holds.json").write_text(json.dumps(holds or {}), encoding="utf-8")
    return backlog, board


def _states(tmp_path, **kw):
    backlog, board = _setup(tmp_path, **kw)
    items, malformed = ready.parse_backlog(backlog)
    items = ready.classify(items, ready._load(board / "state.json"), ready._load(board / "holds.json"))
    return {i["id"]: i for i in items}, malformed


def test_each_state(tmp_path):
    holds = {"T-004": {"reason": "waits on Ryan"}, "T-009": {"reason": "later", "until": "2000-01-01T00:00Z"}}
    s, malformed = _states(tmp_path, holds=holds)
    assert s["T-001"]["state"] == "ready"
    assert s["T-002"]["state"] == "blocked" and s["T-002"]["why"] == "waits on T-003"
    assert s["T-003"]["state"] == "active"
    assert s["T-004"]["state"] == "held"
    assert s["T-005"]["state"] == "done" and s["T-010"]["state"] == "done"
    assert s["T-006"]["state"] == "ready"
    assert s["T-007"]["state"] == "ready"
    assert s["T-009"]["state"] == "ready"
    assert "T-008" not in s and malformed == 1


def test_live_pane_blocks(tmp_path):
    state = {"sessions": [{"pane": "t-999", "state": "working"}]}
    s, _ = _states(tmp_path, state=state)
    assert s["T-007"]["state"] == "blocked"


def test_main_prints_ready_rows_and_count(tmp_path, capsys):
    backlog, board = _setup(tmp_path, holds={"T-004": {}})
    assert ready.main(["--backlog", str(backlog), "--board", str(board)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[4]{id,title,source}:")
    assert "count: 4 of 4 total" in out
    assert "skipped: 1 backlog line(s)" in out
    assert "T-004" not in out and "T-002" not in out


def test_limit_all_and_json(tmp_path, capsys):
    backlog, board = _setup(tmp_path)
    base = ["--backlog", str(backlog), "--board", str(board)]
    ready.main(base + ["--limit", "2"])
    assert "count: 2 of 5 total" in capsys.readouterr().out
    ready.main(base + ["--all"])
    out = capsys.readouterr().out
    assert "id,state,why" in out and "blocked" in out and "count: 9 of 9 total" in out
    ready.main(base + ["--json", "--limit", "1"])
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 1 and data["total"] == 5 and data["rows"][0]["id"] == "T-001"


def test_read_only(tmp_path):
    backlog, board = _setup(tmp_path)
    before = {p: p.read_bytes() for p in list(backlog.glob("*")) + list(board.glob("*"))}
    ready.main(["--backlog", str(backlog), "--board", str(board), "--all"])
    assert before == {p: p.read_bytes() for p in before}
