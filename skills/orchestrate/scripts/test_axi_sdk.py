import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import axi_sdk
from axi_sdk import AxiError, exit_code_for, fast_version, run_axi_cli

HERE = Path(__file__).parent


def hello(args):
    """hello: say hi to NAME.

    flags: none
    """
    if args and args[0].startswith("-"):
        raise AxiError(f"Unknown flag: {args[0]}", "VALIDATION_ERROR", ["Run `demo hello --help`"])
    return {"greeting": f"hi {args[0] if args else 'you'}"}


def boom(args):
    """boom: always fails."""
    raise RuntimeError("kaboom")


def run(argv, **kw):
    buf = io.StringIO()
    code = run_axi_cli(
        lambda: {"count": 3},
        {"hello": hello, "boom": boom},
        description="demo tool",
        version="1.2.3",
        argv=argv,
        stdout=buf,
        prog="demo.py",
        **kw,
    )
    return code, buf.getvalue()


def test_no_args_prints_home():
    code, out = run([])
    assert code == 0
    assert "bin:" in out and "demo.py" in out.splitlines()[0]
    assert "description: demo tool" in out and "count: 3" in out


def test_home_string_body_follows_header():
    buf = io.StringIO()
    run_axi_cli(lambda: "live: yes", {}, description="d", argv=[], stdout=buf, prog="t.py")
    lines = buf.getvalue().splitlines()
    assert lines[0].startswith("bin:") and lines[1:] == ["description: d", "live: yes"]


def test_empty_string_argv_is_home():
    assert run([""])[0] == 0 and "count: 3" in run([""])[1]


def test_home_dict_cannot_override_header():
    buf = io.StringIO()
    run_axi_cli(lambda: {"bin": "evil", "description": "evil", "n": 1}, {}, description="real",
                argv=[], stdout=buf, prog="t.py")
    out = buf.getvalue()
    assert "evil" not in out and "description: real" in out and "n: 1" in out


class PipeBroken:
    def write(self, _):
        raise BrokenPipeError


@pytest.mark.parametrize("argv", [["--help"], ["--version"], []])
def test_broken_pipe_exits_0(argv):
    assert run_axi_cli(lambda: "x", {}, version="1", argv=argv, stdout=PipeBroken()) == 0


def test_bin_name_is_absolute_and_collapses_home(monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\YOURNAME")
    monkeypatch.setenv("HOME", "/home/ryan")
    home = Path("~").expanduser()
    inside = str(home / "x" / "t.py")
    assert axi_sdk._bin_name(inside.upper() if sys.platform == "win32" else inside).startswith("~")
    assert axi_sdk._bin_name(str(home) + "c" + os.sep + "t.py") == os.path.abspath(str(home) + "c" + os.sep + "t.py")
    assert os.path.isabs(axi_sdk._bin_name("t.py")) or axi_sdk._bin_name("t.py").startswith("~")


def test_command_without_docstring_gets_fallback_help_and_does_not_run():
    ran = []

    def bare(args):
        ran.append(args)
        return {"args": args}

    buf = io.StringIO()
    code = run_axi_cli(lambda: "x", {"bare": bare}, argv=["bare", "--help"], stdout=buf, prog="t.py")
    assert code == 0 and buf.getvalue() == "t.py bare: no help text yet\n" and ran == []


def test_missing_toon_format_gives_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "toon_format", None)
    code, out = run(["hello", "bob"])
    assert code == 1 and "toon_format is missing" in out and "requirements.txt" in out


def test_empty_version_is_error():
    buf = io.StringIO()
    assert run_axi_cli(lambda: "x", {}, version="", argv=["-v"], stdout=buf) == 2


def test_unknown_flag_exits_2_with_help_line():
    code, out = run(["--bogus"])
    assert code == 2
    assert "error: Flags must come after the command" in out
    assert any(line.startswith("help[") for line in out.splitlines())


def test_unknown_command_exits_2_with_help_line():
    code, out = run(["nope"])
    assert code == 2 and "help[1]: Run `demo.py --help`" in out


def test_command_validation_error_exits_2():
    code, out = run(["hello", "--x"])
    assert code == 2 and "help[1]: Run `demo hello --help`" in out


def test_other_error_exits_1():
    code, out = run(["boom"])
    assert code == 1 and "error: kaboom" in out and "code: UNKNOWN" in out


def test_command_runs():
    assert run(["hello", "bob"]) == (0, "greeting: hi bob\n")


def test_top_level_help_lists_commands():
    code, out = run(["--help"])
    assert code == 0 and "hello  hello: say hi to NAME." in out and "boom  boom: always fails." in out


def test_subcommand_help_prints_docstring():
    code, out = run(["hello", "--help"])
    assert code == 0 and out.startswith("hello: say hi to NAME.") and "flags: none" in out


def test_top_level_help_prints_examples_when_given():
    buf = io.StringIO()
    run_axi_cli(lambda: "x", {"hello": hello}, argv=["--help"], stdout=buf, prog="t.py", examples=["t.py hello bob"])
    assert "examples:\n  t.py hello bob" in buf.getvalue()


@pytest.mark.parametrize("flag", ["-v", "-V", "--version"])
def test_version_flags(flag):
    assert run([flag]) == (0, "1.2.3\n")


def test_version_not_configured_is_error():
    buf = io.StringIO()
    code = run_axi_cli(lambda: "x", {}, argv=["--version"], stdout=buf)
    assert code == 2 and "help" not in buf.getvalue() and "VALIDATION_ERROR" in buf.getvalue()


def test_exit_code_for():
    assert exit_code_for(AxiError("m", "VALIDATION_ERROR")) == 2
    assert exit_code_for(AxiError("m", "OTHER")) == 1
    assert exit_code_for(ValueError("m")) == 1


def test_fast_version_only_handles_lone_flag():
    buf = io.StringIO()
    assert fast_version(["--version"], "9.9", buf) and buf.getvalue() == "9.9\n"
    for argv in ([], ["x", "--version"], ["--version", "x"], ["--help"]):
        assert not fast_version(argv, "9.9", buf)
    assert buf.getvalue() == "9.9\n"


def test_version_answers_without_importing_the_app(tmp_path):
    (tmp_path / "heavy_app.py").write_text("raise SystemExit('APP IMPORTED')\n")
    tool = tmp_path / "tool.py"
    tool.write_text(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(HERE)!r})
            from axi_sdk import fast_version
            if not fast_version(sys.argv[1:], "4.5.6"):
                import heavy_app
            """
        )
    )
    ok = subprocess.run([sys.executable, str(tool), "--version"], capture_output=True, text=True, cwd=tmp_path)
    assert (ok.returncode, ok.stdout, ok.stderr) == (0, "4.5.6\n", "")
    bad = subprocess.run([sys.executable, str(tool), "x"], capture_output=True, text=True, cwd=tmp_path)
    assert "APP IMPORTED" in bad.stderr


def test_module_under_200_lines_and_no_toon_import_at_top():
    src = (HERE / "axi_sdk.py").read_text()
    assert len(src.splitlines()) < 200
    assert "toon_format" not in src.split("def render(")[0]
    assert axi_sdk.VERSION_FLAGS == ("-v", "-V", "--version")
