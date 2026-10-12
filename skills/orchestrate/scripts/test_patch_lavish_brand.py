"""patch_lavish_brand.py applies cleanly to lavish-axi 0.1.84, is idempotent, and the patched server
serves the Bridge chrome. Runs against a COPY of the pristine dist (see lavish_testkit.py).

    py -m pytest test_patch_lavish_brand.py
"""
import os
import re
import urllib.request

import pytest

import lavish_chat_testkit as C
import lavish_testkit as K

pytestmark = pytest.mark.skipif(not K.available(), reason="no pristine lavish-axi dist on this box")


@pytest.fixture()
def root():
    r = K.make_root("lav-brand-")
    try:
        yield r
    finally:
        K.remove_tree(r)


@pytest.fixture()
def pkg(root):
    return K.make_pkg(root)


def test_not_patched_then_patched_then_idempotent(pkg):
    assert C.run_brand(pkg, check=True).returncode == 1
    first = C.run_brand(pkg)
    assert first.returncode == 0, first.stdout + first.stderr
    for line in ("patched gate", "patched cache-control", "patched home-button html",
                 "patched top-navigation", "patched auto-flush", "patched debrand text",
                 "patched mic-permission", "patched brass accent", "patched panel width"):
        assert line in first.stdout, line
    checked = C.run_brand(pkg, check=True)
    assert checked.returncode == 0, checked.stdout
    assert "not patched" not in checked.stdout
    again = C.run_brand(pkg)
    assert again.returncode == 0 and "patched " not in again.stdout.replace("already patched", "")
    assert again.stdout.count("already patched") >= 12


def test_patched_files_parse(pkg):
    C.apply_chain(pkg, upto="brand")
    assert C.node_check(C.dist_file(pkg, "cli.mjs")).returncode == 0
    r = C.node_check(C.dist_file(pkg, "chrome-client.js"))
    assert r.returncode == 0, r.stderr


def test_dropped_subpatches_are_really_upstream():
    """Each sub-patch dropped for 0.1.84 is dropped because the pristine dist does the same thing."""
    dist = K.pristine_dist()
    cli = open(os.path.join(dist, "cli.mjs"), encoding="utf-8").read()
    client = open(os.path.join(dist, "chrome-client.js"), encoding="utf-8").read()
    assert "new EventSource" not in client and "new WebSocket(" in client       # patch_sse
    assert "allow-popups-to-escape-sandbox" in cli                              # links popup half
    assert "showQueuedSendFailure(" in client and "SEND_FAILED_COPY" in client  # patch_send_error
    assert "receiptNoteText" in client and "No agent is listening" in client    # waiting bubble
    assert 'if (ended) return;\n  if (terminalSubmission) {' in client          # sendQueued: no presence gate


def test_chrome_text_has_no_visible_lavish(pkg):
    C.apply_chain(pkg, upto="brand")
    client = C.read(pkg, "chrome-client.js")
    cli = C.read(pkg, "cli.mjs")
    for frag in ("Lavish is not running.", "Lavish did not answer", "Lavish was updated.",
                 "Lavish is waiting for fonts", "poll for updates from Lavish",
                 "This review is open in another Lavish tab", "Lavish could not finish loading"):
        assert frag not in client and frag not in cli, frag
    assert "poll for updates from Bridge." in cli
    assert 'title: title || "Bridge Editor"' in cli


def test_unknown_version_fails_closed(pkg):
    cli = C.dist_file(pkg, "cli.mjs")
    src = open(cli, encoding="utf-8").read().replace("displayPathParts(session.file)", "displayPathPartsX(session.file)")
    open(cli, "w", encoding="utf-8", newline="\n").write(src)
    r = C.run_brand(pkg)
    assert r.returncode == 1 and "anchor count 0" in r.stdout
    assert "/* patched: artifact title in the bar */" not in open(cli, encoding="utf-8").read()


def test_server_serves_bridge_chrome(pkg, root):
    C.apply_chain(pkg, upto="brand")
    srv, base, key, _ = C.start(pkg, root)
    try:
        with urllib.request.urlopen(f"{base}/session/{key}", timeout=20) as r:
            html = r.read().decode()
            assert r.headers.get("cache-control") == "no-cache"
        assert "<title>Bridge Editor</title>" in html          # the test page has no <title>
        assert "Lavish</span>" not in html
        assert 'id="podiumHomeBtn"' in html
        assert "allow-top-navigation-by-user-activation" in html
        assert re.search(r'id="annotation" type="button" aria-pressed="true"', html)
        assert "layout-gate-active" not in html.split("<body class=\"")[1].split('"')[0]
        for asset in ("chrome.css", "chrome-client.js"):
            with urllib.request.urlopen(f"{base}/{asset}", timeout=20) as r:
                assert r.headers.get("cache-control") == "no-cache"
                body = r.read().decode()
            assert ("Podium brand palette" in body) if asset == "chrome.css" else ("patched: Send to Agent shows a spinner" in body)
    finally:
        srv.kill()
