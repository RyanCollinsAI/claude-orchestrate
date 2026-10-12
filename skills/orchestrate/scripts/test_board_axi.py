"""AXI pass on board.py: --version, exit codes, --format toon, counts, per-command help.

Every call runs board.py in a subprocess against a temp ORCH_BOARD_DIR, so the live board is never touched.
    py -m pytest test_board_axi.py
"""
import os
import subprocess
import sys

import pytest
import toon_format

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD_PY = os.path.join(HERE, "board.py")


@pytest.fixture
def run(tmp_path):
    def go(*args):
        env = dict(os.environ, ORCH_BOARD_DIR=str(tmp_path), PODIUM_SLACK="0")
        r = subprocess.run([sys.executable, BOARD_PY, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=60, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout, r.stderr

    return go


@pytest.mark.parametrize("flag", ["--version", "-v", "-V"])
def test_version_is_one_line_rc0(run, flag):
    assert run(flag) == (0, "1.0.0\n", "")


@pytest.mark.parametrize("fmt", ["text", "toon"])
@pytest.mark.parametrize("flag", ["--version", "-v", "-V"])
def test_version_same_with_either_format(run, flag, fmt):
    assert run(flag, "--format", fmt) == (0, "1.0.0\n", "")
    assert run("--format", fmt, flag) == (0, "1.0.0\n", "")


def test_toon_bin_is_the_real_script_path_from_any_cwd(run, tmp_path):
    env = dict(os.environ, ORCH_BOARD_DIR=str(tmp_path), PODIUM_SLACK="0")
    r = subprocess.run([sys.executable, BOARD_PY, "--format", "toon"], capture_output=True, text=True,
                       encoding="utf-8", env=env, cwd=str(tmp_path), stdin=subprocess.DEVNULL)
    bin_line = r.stdout.splitlines()[0]
    assert "board.py" in bin_line and os.path.basename(str(tmp_path)) not in bin_line


def test_toon_unknown_flag_named_before_missing_positionals(run):
    rc, out, _ = run("answer", "--bogus", "--format", "toon")
    assert rc == 2 and "unrecognized arguments: --bogus" in out and "required" not in out


def test_toon_unknown_flag_lists_valid_flags_and_template(run):
    rc, out, _ = run("answer", "--bogus", "--format", "toon")
    err = toon_format.loads(out)
    assert rc == 2 and "unrecognized arguments: --bogus" in err["error"]
    assert any(h.startswith("Usage: py board.py answer") for h in err["help"])
    assert "Valid flags: --by, --reason" in err["help"]
    assert 'Example: py board.py answer q5 "A"' in err["help"]


def test_toon_missing_argument_gives_runnable_template(run):
    rc, out, stderr = run("answer", "--format", "toon")
    err = toon_format.loads(out)
    assert rc == 2 and stderr == "" and "required" in err["error"]
    assert "Usage: py board.py answer [-h] [--by BY] [--reason REASON] qid answer" in err["help"]
    assert 'Example: py board.py answer q5 "A"' in err["help"]


def test_toon_bad_flag_on_render_shows_usage(run):
    rc, out, _ = run("render", "--bogus", "--format", "toon")
    err = toon_format.loads(out)
    assert rc == 2 and "unrecognized arguments: --bogus" in err["error"]
    assert any(h.startswith("Usage: py board.py render") for h in err["help"])


def test_text_mode_errors_unchanged(run):
    rc, out, err = run("answer")
    assert rc == 2 and out == "" and "the following arguments are required: qid, answer" in err


def test_default_no_args_still_prints_help_rc0(run):
    rc, out, _ = run()
    assert rc == 0 and out.startswith("usage: board") and "add-question" in out


def test_bad_question_id_is_usage_error_rc2_on_stderr(run):
    rc, out, err = run("answer", "q-nope", "A")
    assert rc == 2 and out == "" and "no question q-nope on the board" in err


def test_show_without_input_rc2(run):
    rc, _, err = run("show")
    assert rc == 2 and "show needs --file or --text" in err


def test_conflict_stays_rc1(run):
    assert run("add-question", "--id", "q1", "--title", "t", "--context", "c")[0] == 0
    rc, _, err = run("add-question", "--id", "q1", "--title", "t", "--context", "c")
    assert rc == 1 and "already exists" in err


def test_old_output_unchanged_by_default(run):
    assert run("add-question", "--id", "q1", "--title", "Ship it", "--context", "c")[1] == "added q1: Ship it\n"
    assert run("answer", "q1", "A")[1] == "answered q1: A\n"
    assert run("answer", "q1", "A")[1] == "q1 was already answered A; nothing changed\n"


def test_subcommand_help_has_examples(run):
    rc, out, _ = run("answer", "--help")
    assert rc == 0 and "examples:" in out and "py board.py answer q5" in out


def test_toon_home_counts_open_of_total(run):
    run("add-question", "--id", "q1", "--title", "First", "--context", "c", "--pick", "A")
    run("add-question", "--id", "q2", "--title", "Second", "--context", "c")
    run("answer", "q1", "A")
    rc, out, err = run("--format", "toon")
    assert rc == 0 and err == ""
    assert "bin:" in out.splitlines()[0] and "open_questions: 1 of 2" in out
    assert "q2" in out and "Second" in out and "help[3]" in out


def test_toon_home_empty_state_is_definitive(run):
    rc, out, _ = run("--format", "toon")
    assert rc == 0 and "open_questions: 0 of 0" in out and "none open" in out and "none listed" in out


def test_toon_flag_works_after_the_command(run):
    run("add-question", "--id", "q1", "--title", "T", "--context", "c")
    rc, out, _ = run("answer", "q1", "A", "--format", "toon")
    assert rc == 0 and "command: answer" in out and 'result: "answered q1: A"' in out and "help[1]" in out


def test_toon_error_goes_to_stdout_with_help_rc2(run):
    rc, out, err = run("answer", "q-nope", "A", "--format", "toon")
    assert rc == 2 and err == ""
    assert "error: no question q-nope on the board" in out and "code: VALIDATION_ERROR" in out
    assert "help[1]" in out and "--format toon" in out


def test_toon_conflict_is_rc1(run):
    run("add-question", "--id", "q1", "--title", "t", "--context", "c")
    rc, out, _ = run("add-question", "--id", "q1", "--title", "t", "--context", "c", "--format", "toon")
    assert rc == 1 and "already exists" in out and "code: ERROR" in out


def test_toon_unknown_flag_rc2_on_stdout(run):
    rc, out, err = run("render", "--bogus", "--format", "toon")
    assert rc == 2 and err == "" and "error:" in out and "Run `py board.py render --help` for examples" in out


def test_toon_unknown_command_rc2(run):
    rc, out, _ = run("bogus", "--format", "toon")
    assert rc == 2 and "Unknown command: bogus" in out


def test_toon_sessions_has_count_and_empty_state(run):
    rc, out, _ = run("sessions", "--format", "toon")
    assert rc == 0 and "count: 0" in out and "sessions: none listed" in out
    run("session", "kun-k3", "--doing", "AXI pass", "--model", "sonnet", "--state", "working")
    rc, out, _ = run("sessions", "--format", "toon")
    assert "count: 1" in out and "sessions[1]{pane,state,model,doing}:" in out and "kun-k3" in out


def test_toon_per_command_help_exits_0(run):
    rc, out, _ = run("answer", "--help", "--format", "toon")
    assert rc == 0 and "examples:" in out
    rc, out, _ = run("--help", "--format", "toon")
    assert rc == 0 and "answer" in out and "digest" in out


def test_toon_version(run):
    assert run("--version", "--format", "toon") == (0, "1.0.0\n", "")


def test_long_title_is_cut_with_total(run):
    run("add-question", "--id", "q1", "--title", "x" * 200, "--context", "c")
    rc, out, _ = run("--format", "toon")
    assert "...(+120 chars)" in out


def test_bad_format_value_rc2(run):
    rc, _, err = run("--format", "xml")
    assert rc == 2 and "--format must be text or toon" in err
