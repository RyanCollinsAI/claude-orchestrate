"""Small AXI CLI contract for Python tools: home view, error contract, fast --version, per-command --help.

Rebuilt in Python from axi-sdk-js (https://github.com/kunchenguid/axi), MIT License,
Copyright (c) 2026 kunchenguid. Permission is granted to use, copy, modify and
distribute it, provided this notice stays with the copies. The software is provided
"AS IS", without warranty of any kind. Full text: axi/packages/axi-sdk-js/LICENSE.

Contract:
  no args         -> home view (header + live data), exit 0
  --version       -> version only, answered before the app is imported (fast_version)
  --help          -> top-level help; `<cmd> --help` -> that command's docstring
  mistakes        -> TOON error with a `help:` fix on STDOUT, exit 2 (usage) or 1 (other)

Keep this module free of heavy imports: fast_version() must work before the app loads.
Dependency: toon-format (requirements.txt). It loads on first render, never for --version.
"""
import os
import sys

VERSION_FLAGS = ("-v", "-V", "--version")


class AxiError(Exception):
    def __init__(self, message, code="ERROR", suggestions=()):
        super().__init__(message)
        self.message, self.code, self.suggestions = message, code, list(suggestions)


def exit_code_for(err):
    return 2 if isinstance(err, AxiError) and err.code == "VALIDATION_ERROR" else 1


def fast_version(argv, version, stdout=None):
    """Print version and return True for a lone -v/-V/--version; else print nothing, return False."""
    if len(argv) != 1 or argv[0] not in VERSION_FLAGS:
        return False
    (stdout or sys.stdout).write(f"{version}\n")
    return True


def render(output):
    if isinstance(output, str):
        return output
    try:
        import toon_format  # lazy: keeps fast_version dependency-free
    except ImportError as err:
        raise RuntimeError("toon_format is missing; run `py -m pip install -r requirements.txt` in the scripts folder") from err

    return toon_format.dumps(output)


def render_error(err):
    if isinstance(err, AxiError):
        out = {"error": err.message, "code": err.code}
        if err.suggestions:
            out["help"] = err.suggestions
    else:
        out = {"error": str(err) or type(err).__name__, "code": "UNKNOWN"}
    try:
        return render(out)
    except RuntimeError:  # toon_format missing: the error itself must still print
        return "\n".join(f"{k}: {v}" for k, v in out.items() if isinstance(v, str))


def _bin_name(argv0):
    path, home = os.path.abspath(argv0), os.path.expanduser("~")
    if os.path.normcase(path).startswith(os.path.normcase(home) + os.sep):
        return "~" + path[len(home):]
    return path


def _first_line(fn):
    return ((fn.__doc__ or "").strip().splitlines() or [""])[0]


def run_axi_cli(home, commands, *, description="", version=None, argv=None, stdout=None, prog=None, examples=()):
    """Run a tool. home(): str|dict for the no-args view. commands: {name: fn(args) -> str|dict}.

    A command's docstring is its --help text. Returns the exit code; the caller does sys.exit().
    """
    out = stdout or sys.stdout
    argv = sys.argv[1:] if argv is None else list(argv)
    prog = prog or sys.argv[0]
    name = os.path.basename(prog)

    def fail(err):
        out.write(render_error(err) + "\n")
        return exit_code_for(err)

    def usage(message, *suggestions):
        return fail(AxiError(message, "VALIDATION_ERROR", suggestions))

    try:
        if len(argv) == 1 and argv[0] == "--help":
            lines = [f"{name}: {description}", "commands:"]
            lines += [f"  {n}  {_first_line(fn)}" for n, fn in commands.items()]
            if examples:
                lines += ["examples:"] + [f"  {e}" for e in examples]
            out.write("\n".join(lines) + "\n")
            return 0
        if len(argv) == 1 and argv[0] in VERSION_FLAGS:
            if not version:
                return usage("Version is not configured for this tool")
            fast_version(argv, version, out)
            return 0

        if not argv or not argv[0]:
            body = home()
            header = {"bin": _bin_name(prog), "description": description}
            if isinstance(body, dict):
                body = {k: v for k, v in body.items() if k not in header}
            out.write(render({**header, **body} if isinstance(body, dict) else header) + "\n")
            if isinstance(body, str):
                out.write(body + "\n")
            return 0

        cmd, args = argv[0], argv[1:]
        if cmd.startswith("-"):
            return usage(
                "Flags must come after the command",
                f"Run `{name} <command> [args] [flags]`",
                f"Move `{cmd}` after the command instead of before it",
            )
        if cmd not in commands:
            return usage(f"Unknown command: {cmd}", f"Run `{name} --help` to see available commands")
        if "--help" in args:
            out.write((commands[cmd].__doc__ or f"{name} {cmd}: no help text yet").strip() + "\n")
            return 0
        out.write(render(commands[cmd](args)) + "\n")
        return 0
    except BrokenPipeError:
        return 0
    except Exception as err:  # the contract: every failure prints a structured error
        return fail(err)
