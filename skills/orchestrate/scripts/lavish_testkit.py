"""Shared helpers for the lavish-axi patch tests. Never touches the global install or ~/.lavish-axi.

A test copies a PRISTINE (unpatched) lavish-axi dist into a temp dir, points the patch scripts at the
copy with LAVISH_CLI_MJS / LAVISH_DIST, and starts servers from the copy on a free port with a throwaway
state dir (LAVISH_AXI_STATE_DIR).

The pristine dist is found in this order: $LAVISH_PRISTINE_DIST, D:\\lavish-canary-pristine-dist, then the
global npm install if its cli.mjs carries no patch marker. Its node_modules (needed to run a server) is
found from $LAVISH_NODE_MODULES, D:\\lavish-pristine-install, then the global install.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import _winapi

GLOBAL_PKG = os.path.expanduser(r"~\AppData\Roaming\npm\node_modules\lavish-axi")
DIST_CANDIDATES = [
    os.environ.get("LAVISH_PRISTINE_DIST"),
    r"D:\lavish-canary-pristine-dist",
    os.path.join(GLOBAL_PKG, "dist"),
]
MODULES_CANDIDATES = [
    os.environ.get("LAVISH_NODE_MODULES"),
    r"D:\lavish-pristine-install\node_modules\lavish-axi\node_modules",
    os.path.join(GLOBAL_PKG, "node_modules"),
]


def _first(paths, probe):
    for p in paths:
        if p and os.path.exists(os.path.join(p, probe)):
            return p
    return None


def pristine_dist():
    for p in DIST_CANDIDATES:
        if p and os.path.exists(os.path.join(p, "cli.mjs")):
            if "/* patched:" not in open(os.path.join(p, "cli.mjs"), encoding="utf-8").read():
                return p
    return None


def node_modules():
    return _first(MODULES_CANDIDATES, "express")


def available():
    return pristine_dist() is not None and node_modules() is not None


def make_pkg(root, name="pkg"):
    """Copy the pristine dist to <root>/<name>/dist with package.json and a node_modules junction."""
    pkg = os.path.join(root, name)
    os.makedirs(pkg)
    shutil.copytree(pristine_dist(), os.path.join(pkg, "dist"))
    mods = node_modules()
    shutil.copy(os.path.join(os.path.dirname(mods), "package.json"), pkg)
    _winapi.CreateJunction(mods, os.path.join(pkg, "node_modules"))
    return pkg


def remove_tree(root):
    for dirpath, dirnames, _ in os.walk(root):
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if d == "node_modules" and os.path.isjunction(full):
                os.rmdir(full)
    shutil.rmtree(root, ignore_errors=True)


def make_root(prefix):
    return tempfile.mkdtemp(prefix=prefix)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def patch_env(pkg):
    dist = os.path.join(pkg, "dist")
    return {**os.environ, "LAVISH_CLI_MJS": os.path.join(dist, "cli.mjs"), "LAVISH_DIST": dist}


def run_patch(script, pkg, *args):
    return subprocess.run([sys.executable, script, *args], capture_output=True, text=True, env=patch_env(pkg),
                          stdin=subprocess.DEVNULL)


def node_check(path):
    return subprocess.run(["node", "--check", path], capture_output=True, text=True, stdin=subprocess.DEVNULL)


def start_server(pkg, state_dir, port, extra_args=(), node_args=()):
    os.makedirs(state_dir, exist_ok=True)
    env = {**os.environ, "LAVISH_AXI_STATE_DIR": state_dir, "LAVISH_AXI_PORT": str(port),
           "LAVISH_AXI_NO_OPEN": "1", "LAVISH_AXI_TELEMETRY": "off", "LAVISH_AXI_IDLE_TIMEOUT_MS": "0"}
    log = open(os.path.join(state_dir, "server.stdout.log"), "w")
    srv = subprocess.Popen(["node", *node_args, os.path.join(pkg, "dist", "server.mjs"), "server", "--port", str(port),
                            *extra_args], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    log.close()
    base = f"http://127.0.0.1:{port}"
    for _ in range(200):
        if srv.poll() is not None:
            break
        try:
            urllib.request.urlopen(base + "/health", timeout=1)
            return srv, base
        except Exception:
            time.sleep(0.1)
    srv.kill()
    raise RuntimeError("server did not start: " + open(os.path.join(state_dir, "server.stdout.log")).read()[-2000:])


def post(base, path, body, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "origin": base, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def get_json(base, path):
    return json.loads(urllib.request.urlopen(base + path, timeout=60).read())


def open_session(base, art_dir, name):
    import hashlib
    f = os.path.join(art_dir, name)
    open(f, "w").write("<h1>x</h1>")
    status = post(base, "/api/sessions", {"file": f})
    key = hashlib.sha256(os.path.realpath(f).encode()).hexdigest()[:16]
    return key, status
