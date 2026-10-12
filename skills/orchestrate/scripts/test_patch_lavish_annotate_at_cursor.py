"""patch_lavish_annotate_at_cursor.py applies cleanly to lavish-axi 0.1.84 and is idempotent.

Runs against a COPY of the pristine dist (see lavish_testkit.py), never the global install.

    py -m pytest test_patch_lavish_annotate_at_cursor.py
"""
import os
import subprocess

import pytest

import lavish_testkit as K

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "patch_lavish_annotate_at_cursor.py")

pytestmark = pytest.mark.skipif(not K.available(), reason="no pristine lavish-axi dist on this box")


@pytest.fixture()
def pkg():
    root = K.make_root("lav-annot-")
    try:
        yield K.make_pkg(root)
    finally:
        K.remove_tree(root)


def test_not_patched_then_patched_then_idempotent(pkg):
    assert K.run_patch(SCRIPT, pkg, "--check").returncode == 1
    first = K.run_patch(SCRIPT, pkg)
    assert first.returncode == 0 and "patched annotate-at-cursor" in first.stdout
    assert K.run_patch(SCRIPT, pkg, "--check").returncode == 0
    again = K.run_patch(SCRIPT, pkg)
    assert again.returncode == 0 and "already patched" in again.stdout


def test_patched_cli_is_valid_js(pkg):
    assert K.run_patch(SCRIPT, pkg).returncode == 0
    r = subprocess.run(["node", "--check", os.path.join(pkg, "dist", "cli.mjs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_unknown_version_fails_closed(pkg):
    cli = os.path.join(pkg, "dist", "cli.mjs")
    src = open(cli, encoding="utf-8").read().replace("function closeCard() {", "function closeCardRenamed() {")
    open(cli, "w", encoding="utf-8", newline="\n").write(src)
    r = K.run_patch(SCRIPT, pkg)
    assert r.returncode == 1 and "anchor count 0" in r.stdout
    assert "/* patched: annotate at cursor */" not in open(cli, encoding="utf-8").read()
