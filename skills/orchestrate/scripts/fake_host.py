"""A fake remote host for test_remote.py: a local stand-in for ssh, scp and the claude CLI.

  fake_host.py ssh  <ssh options> <target> "<remote command>"   runs remote.py's host verbs locally
  fake_host.py scp  <scp options> <src> <dst>                   copies, mapping `target:path` into FAKE_HOME
  fake_host.py claude -p "Read <task> and do exactly what it says." ...   writes the report the task names

Environment: FAKE_HOME (the pretend remote home), FAKE_MODE = ok | timeout | refused | key | hostkey
(any other mode than ok makes ssh and scp print what real ssh prints for that failure and exit 255),
FAKE_CLAUDE_SLEEP (seconds the fake claude stays alive before it writes its report).
"""
import os, re, shlex, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = {
    "timeout": "ssh: connect to host 100.x.y.z port 22: Connection timed out",
    "refused": "ssh: connect to host 100.x.y.z port 22: Connection refused",
    "key": "YOURNAME@100.x.y.z: Permission denied (publickey,keyboard-interactive).",
    "hostkey": "@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@",
}


def fail_if_asked():
    mode = os.environ.get("FAKE_MODE", "ok")
    if mode != "ok":
        print(FAILS[mode], file=sys.stderr)
        sys.exit(255)


def ssh(argv):
    fail_if_asked()
    i = 0
    while argv[i] == "-o":
        i += 2
    cmd = shlex.split(argv[i + 1], posix=True)          # py "<remote.py>" host-verb args...
    env = {**os.environ, "ORCH_REMOTE_HOME": os.environ["FAKE_HOME"]}
    sys.exit(subprocess.run([sys.executable, os.path.join(HERE, "remote.py"), *cmd[2:]],
                            env=env, stdin=sys.stdin).returncode)


def scp(argv):
    fail_if_asked()
    args = [a for a in argv if a != "-p"]
    i = 0
    while args[i] == "-o":
        i += 2
    src, dst = args[i], args[i + 1]
    home = os.environ["FAKE_HOME"]
    remote = lambda p: os.path.join(home, p.split(":", 1)[1]) if re.match(r"^[^\\/:]+:[^\\/]", p) else p
    s, d = remote(src), remote(dst)
    if os.path.isdir(d) or d.endswith(("/", "\\")):
        d = os.path.join(d, os.path.basename(s))
    os.makedirs(os.path.dirname(d), exist_ok=True)
    shutil.copy2(s, d)


def claude(argv):
    prompt = argv[argv.index("-p") + 1]
    task = re.match(r"Read (.+) and do exactly what it says\.", prompt).group(1)
    body = open(task, encoding="utf-8").read()
    report = re.search(r"Write your final report to `([^`]+)`", body).group(1)
    time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("Done: fake task finished\nProof: fake\nNext: none\n")


if __name__ == "__main__":
    {"ssh": ssh, "scp": scp, "claude": claude}[sys.argv[1]](sys.argv[2:])
