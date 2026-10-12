"""Patch the lavish-axi chrome so it looks like Bridge instead of Lavish.

lavish-axi renders its chrome server-side in dist/cli.mjs (createChromeHtml) and styles it
from dist/chrome.css. Ryan asked 2026-09-04 that Bridge stop saying "Lavish EDITOR" at the
top (patches cli.mjs), and 2026-09-04 that the top bar and the right Conversation sidebar
stop looking like near-black Lavish and read as Bridge's own light palette instead (patches
chrome.css, appending an override :root block whose values are lifted from board.py's own
:root vars, keeping board.py's --ask amber as the accent so page and chrome match). The
package has no option for either, so this patches the installed files in place. Idempotent:
run it after every `npm i -g lavish-axi` update. Restart the server afterwards
(`lavish-axi stop`, then open the page again) - the HTML/CSS is read per request but the
cli.mjs module is loaded once.

Ported to lavish-axi 0.1.84 (task F6). Sub-patches DROPPED because 0.1.84 does the same thing:

  - SSE-only-while-visible (patch_sse): the live stream is a WebSocket now (chrome-client.js
    connectLiveEvents, "WebSockets leave the browser's HTTP connection pool free"), and dropping
    it for a hidden tab would now also look like a browser disconnect to the server
    (scheduleBrowserDisconnect in cli.mjs releases the agent's poll).
  - Failed-send red bar (patch_send_error): submitQueuedOnce now catches a failed POST, keeps the
    note queued and shows a persistent hint (SEND_FAILED_COPY, showQueuedSendFailure).
  - "Sent. Waiting for an agent..." bubble and the 15 s sending cap (patch_sending): the sent note
    carries a Seen / Working / Done receipt row whose pre-Seen line says "No agent is listening" /
    "Delivering" (receiptNoteText); the SNAPSHOT_REQUEST_TIMEOUT_MS cap bounds the wait.
  - Presence retry loop in sendQueued (patch_auto_flush_queue, second half): sendQueued has no
    presence gate any more.
  - allow-popups-to-escape-sandbox (patch_links_open, second half): already in the iframe sandbox.

    py patch_lavish_brand.py            # patch both, or say "already patched"
    py patch_lavish_brand.py --check    # exit 0 if both patched, 1 if not
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CLI = os.environ.get("LAVISH_CLI_MJS") or os.path.expanduser(
    r"~\AppData\Roaming\npm\node_modules\lavish-axi\dist\cli.mjs")   # env override: patch a scratch copy
CSS =os.path.join(os.path.dirname(CLI), "chrome.css")
MARK = "/* patched: artifact title in the bar */"
CSS_MARK = "/* patched: Podium brand palette (not lavish near-black) */"
# Same amber board.py uses for its own buttons (--ask), so the chrome and the page match.
CSS_OVERRIDE = CSS_MARK + "\n" + """:root {
  --bg: #f7f7f5;
  --bg-panel: #ffffff;
  --bg-bar: #ffffff;
  --bg-elevated: #fff7ed;
  --fg: #1a1a1a;
  --fg-muted: #6b6b6b;
  --fg-dim: #6b6b6b;
  --fg-faint: #8c8c8c;
  --fg-label: #6b6b6b;
  --border: #e4e2dd;
  --border-subtle: #ececea;
  --border-strong: #cfcdc6;
  --accent: #b45309;
  --accent-hover: #92400e;
  --accent-ink: #ffffff;
}
body.lavish {
  color-scheme: light;
}
"""

# createChromeHtml gets one caller (the /session/:key route). That route already extracts the
# artifact's <title> (extractArtifactHead) but renders it as "<title> · Lavish"; this passes the bare
# title as the tab title and as a new artifactTitle option the bar uses for its brand mark.
ANCHOR = "const { head: pathHead, tail: pathTail } = displayPathParts(session.file);\n"
ARTIFACT_TITLE_LINE = "  const artifactTitleHtml = escapeHtml2(artifactTitle);\n"
INSERT = ANCHOR + "  " + MARK + "\n" + ARTIFACT_TITLE_LINE
ROUTE_TITLE_OLD = '          title: title ? `${title} \\xB7 Lavish` : "Lavish Editor",\n'
ROUTE_TITLE_NEW = '          title: title || "Bridge Editor",\n          artifactTitle: title,\n'
PARAM_TITLE_OLD = '  title = "Lavish Editor",\n  artifactRevision = 0,\n'
PARAM_TITLE_NEW = '  title = "Bridge Editor",\n  artifactTitle = "",\n  artifactRevision = 0,\n'
OLD_BRAND = '<span class="brand-mark">Lavish</span><span class="brand-support">Editor</span>'
NEW_BRAND = (
    "${artifactTitleHtml ? `<span class=\"brand-mark\">${artifactTitleHtml}</span>` : "
    "`" + OLD_BRAND + "`}"
)


def patch_cli(check: bool) -> int:
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    patched = MARK in src
    if check:
        print("cli.mjs: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("cli.mjs: already patched")
        return 0
    for needle in (ANCHOR, ROUTE_TITLE_OLD, PARAM_TITLE_OLD, OLD_BRAND):
        if src.count(needle) != 1:
            print(f"anchor count {src.count(needle)} != 1 for: {needle[:60]}")
            return 1
    out = (src.replace(ANCHOR, INSERT).replace(ROUTE_TITLE_OLD, ROUTE_TITLE_NEW)
              .replace(PARAM_TITLE_OLD, PARAM_TITLE_NEW).replace(OLD_BRAND, NEW_BRAND))
    if "readFileSync" not in src.split("createChromeHtml")[0]:
        print("readFileSync is not imported in cli.mjs; refusing to patch")
        return 1
    open(CLI, "w", encoding="utf-8", newline="\n").write(out)
    print(f"patched {CLI}")
    return 0


GATE_MARK = "/* patched: layout gate is opt-in (gate=1), never default */"
GATE_ANCHOR = "function shouldEnableLayoutGate(query = {}) {\n  const noGate = "
GATE_INSERT = (
    "function shouldEnableLayoutGate(query = {}) {\n"
    "  " + GATE_MARK + "\n"
    "  if (!isTruthyFlag(query.gate ?? query.layoutGate ?? query.layout_gate)) return false;\n"
    "  const noGate = "
)


def patch_gate(check: bool) -> int:
    """Ryan, 2026-09-07, on the 'Checking layout. One moment. Lavish is waiting for fonts...'
    curtain: 'says lavish on here, get rid of lavish'. The package only offers a per-open
    --no-gate flag, so this flips the default: the curtain shows only when a URL asks for
    it with ?gate=1. Its one caller is the /session/:key route."""
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    patched = GATE_MARK in src
    if check:
        print("cli.mjs gate: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("cli.mjs gate: already patched")
        return 0
    if src.count(GATE_ANCHOR) != 1:
        print(f"anchor count {src.count(GATE_ANCHOR)} != 1 for shouldEnableLayoutGate")
        return 1
    open(CLI, "w", encoding="utf-8", newline="\n").write(src.replace(GATE_ANCHOR, GATE_INSERT))
    print(f"patched gate in {CLI}")
    return 0


CLIENT = os.path.join(os.path.dirname(CLI), "chrome-client.js")


def patch_css(check: bool) -> int:
    if not os.path.exists(CSS):
        print(f"not found: {CSS}")
        return 1
    src = open(CSS, encoding="utf-8").read()
    patched = CSS_MARK in src
    if check:
        print("chrome.css: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("chrome.css: already patched")
        return 0
    out = src.rstrip("\n") + "\n\n" + CSS_OVERRIDE
    open(CSS, "w", encoding="utf-8", newline="\n").write(out)
    print(f"patched {CSS}")
    return 0


CACHE_SESSION_OLD = (
    '      res.type("html").send(\n'
    '        createChromeHtml(session, {\n'
)
CACHE_SESSION_NEW = (
    '      res.set("Cache-Control", "no-cache").type("html").send(\n'
    '        createChromeHtml(session, {\n'
)
CACHE_INDEX_OLD = (
    '      res.type("html").send(injectLavishSdk(html, key, verified.artifact_revision, '
    'verified.artifact_load_token));'
)
CACHE_INDEX_NEW = (
    '      res.set("Cache-Control", "no-cache").type("html").send(injectLavishSdk(html, key, '
    'verified.artifact_revision, verified.artifact_load_token));'
)
# `res.sendFile(file, { dotfiles: "allow" });` appears twice (this route and /whiteboard-assets),
# so the asset route is anchored by its tail, which runs straight into the /events/:key route.
CACHE_ASSET_OLD = (
    '      res.sendFile(file, { dotfiles: "allow" });\n'
    '    } catch (error) {\n'
    '      next(error);\n'
    '    }\n'
    '  });\n'
    '  app.get("/events/:key"'
)
CACHE_ASSET_NEW = (
    '      res.set("Cache-Control", "no-cache");\n'
    '      res.sendFile(file, { dotfiles: "allow", cacheControl: false });\n'
    '    } catch (error) {\n'
    '      next(error);\n'
    '    }\n'
    '  });\n'
    '  app.get("/events/:key"'
)


def patch_cache_control(check: bool) -> int:
    """lavish-axi sent an ETag but no Cache-Control on /session/:key, /artifact/:key/index.html,
    and /artifact/:key/<asset>, so Chrome served the artifact iframe from its memory cache after
    a republish - a plain reload of the learn tab kept showing a copy up to two edits old, and
    only Ctrl+Shift+R picked up the new one. Ryan, 2026-09-07 (task-podium-cache-header). Keeps
    the ETag so an unchanged artifact is still a cheap 304; static package assets (chrome.css,
    chrome-client.js, /design/:asset) are untouched since they only change on a version bump,
    not on every republish.

    0.1.84 still sets none of the three. (Its iframe src now carries artifact_revision and
    artifact_load_token, which also defeats the stale copy for index.html, but a relative asset
    the page loads has no such query, so the header stays.)"""
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    pairs = [
        ("session", CACHE_SESSION_OLD, CACHE_SESSION_NEW),
        ("index", CACHE_INDEX_OLD, CACHE_INDEX_NEW),
        ("asset", CACHE_ASSET_OLD, CACHE_ASSET_NEW),
    ]
    already = all(new in src for _, _, new in pairs)
    if check:
        print("cli.mjs cache-control: " + ("patched" if already else "not patched"))
        return 0 if already else 1
    if already:
        print("cli.mjs cache-control: already patched")
        return 0
    out = src
    for name, old, new in pairs:
        if new in out:
            continue
        if out.count(old) != 1:
            print(f"cache-control anchor count {out.count(old)} != 1 for: {name}")
            return 1
        out = out.replace(old, new)
    open(CLI, "w", encoding="utf-8", newline="\n").write(out)
    print(f"patched cache-control in {CLI}")
    return 0


SENDING_MARK = "/* patched: Send to Agent shows a spinner while a send is in flight */"
SENDING_CSS_MARK = "/* patched: Send to Agent spinner look */"
SENDING_ANCHOR = "  sendButton.disabled = ended || terminalReserved;\n"
SENDING_NEW = (
    SENDING_ANCHOR
    + "  " + SENDING_MARK + "\n"
    "  const sendingNow = queued.some(isPromptSending);\n"
    "  if (sendButton.dataset.sending !== String(sendingNow)) {\n"
    "    sendButton.dataset.sending = String(sendingNow);\n"
    "    sendButton.innerHTML = sendingNow ? '<span class=\"spinner\"></span>Sending...' : \"Send to Agent\";\n"
    "  }\n"
)
SENDING_CSS = SENDING_CSS_MARK + """
#send[data-sending="true"] {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
#send[data-sending="true"] .spinner {
  border-color: rgba(255, 255, 255, 0.35);
  border-top-color: var(--accent-ink);
}
"""


def patch_sending(check: bool) -> int:
    """Ryan, 2026-09-10: 'also need a loading icon on podium when i hit send to agent'. The Send
    button carries a spinner and 'Sending...' while any queued note is committed to a send that has
    not been answered yet (isPromptSending: waiting for its snapshot, queued behind another, or in
    flight). updateSendState runs from render(), which sendQueued and submitQueued already call at
    every transition, so no state of its own is tracked. The note itself reads 'Sending...' in the
    queue upstream, but the button is where he looks."""
    for path in (CLIENT, CSS):
        if not os.path.exists(path):
            print(f"not found: {path}")
            return 1
    src = open(CLIENT, encoding="utf-8").read()
    css = open(CSS, encoding="utf-8").read()
    js_ok = SENDING_MARK in src
    css_ok = SENDING_CSS_MARK in css
    if check:
        print("chrome-client.js sending: " + ("patched" if js_ok else "not patched"))
        print("chrome.css sending: " + ("patched" if css_ok else "not patched"))
        return 0 if js_ok and css_ok else 1
    if js_ok and css_ok:
        print("sending: already patched")
        return 0
    if not js_ok:
        if src.count(SENDING_ANCHOR) != 1:
            print(f"anchor count {src.count(SENDING_ANCHOR)} != 1 for sending patch: {SENDING_ANCHOR[:60]!r}")
            return 1
        open(CLIENT, "w", encoding="utf-8", newline="\n").write(src.replace(SENDING_ANCHOR, SENDING_NEW))
        print(f"patched sending in {CLIENT}")
    if not css_ok:
        open(CSS, "w", encoding="utf-8", newline="\n").write(css.rstrip("\n") + "\n\n" + SENDING_CSS)
        print(f"patched sending look in {CSS}")
    return 0


HOME_MARK = "/* patched: Home button links back to Podium home (v2, podium-own-home aware) */"
OWN_HOME_MARKER = "podium-own-home"
# A page that draws its own Home button says so with a `podium-own-home` marker in its first 8KB;
# that page gets neither the injected pill nor the reserved strip (Ryan: "see how there are like
# two nav bars, we want only one so we have more room to look at the screen").
# Anchored on the artifactTitleHtml line patch_cli inserts, so this needs patch_cli first.
HOME_URL_ANCHOR = ARTIFACT_TITLE_LINE
# Inside .frame, not at <body> level: .frame gets position:relative and its own reserved top strip
# (patch_home_css), so an absolutely-positioned button here sits in genuinely empty space above the
# iframe instead of floating over whatever the artifact renders in its own top-left corner - a
# body-level `position:fixed` button covered a page's own <h1> (measured 2026-09-10, home.html).
HOME_FRAME_ANCHOR = '<div class="frame"><iframe id="artifact"'
HOME_ICON = (
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M3 11.5 12 4l9 7.5"/>'
    '<path d="M5.5 10v9a1 1 0 0 0 1 1H9.5v-6h5v6H17.5a1 1 0 0 0 1-1v-9"/></svg>'
)


def _home_url_block():
    """Reads podium_home.py's HOME_URLFILE for the live Bridge URL, falling back to a plain
    file:// link to home.html itself when nobody has opened it through lavish-axi yet (or
    podium_home.py is missing entirely - a fresh box with the patch applied before that script
    exists must still get a working, if inert, button)."""
    import podium_home as PH
    home_file_url = "file:///" + os.path.abspath(PH.HOME).replace("\\", "/")
    urlfile = os.path.abspath(PH.HOME_URLFILE).replace("\\", "\\\\")
    own = ("  const podiumOwnHome = (() => { try { return readFileSync(session.file, \"utf8\")"
           ".slice(0, 8192).includes(\"" + OWN_HOME_MARKER + "\"); } catch { return false; } "
           "})();\n")
    return (
        "  " + HOME_MARK + "\n" + own
        + "  const podiumHomeUrl = (() => { try { const u = readFileSync(\"" + urlfile + "\", "
        "\"utf8\").trim(); return u || " + repr(home_file_url).replace("'", '"') + "; } "
        "catch { return " + repr(home_file_url).replace("'", '"') + "; } })();\n"
        "  const podiumHomeUrlHtml = podiumHomeUrl.replace(/\"/g, \"&quot;\");\n"
    )


def _home_pill(url_expr):
    return ('<a class="podium-home-btn" id="podiumHomeBtn" href="' + url_expr + '" '
            'target="_top" rel="noopener" title="Bridge home">' + HOME_ICON + '<span>Home</span></a>')


FRAME_INSERT = (
    '<div class="frame${podiumOwnHome ? " no-home-strip" : ""}">'
    '${podiumOwnHome ? "" : `' + _home_pill("${podiumHomeUrlHtml}") + '`}'
    '<iframe id="artifact"'
)


def patch_home_html(check: bool) -> int:
    """The button itself: a small pill in the top-left corner of `.frame` (the artifact viewing
    pane, not the whole chrome), absolutely positioned inside a reserved top strip patch_home_css
    adds to `.frame` - so it sits in genuinely empty space above the iframe rather than floating
    over whatever the page itself renders at its own top-left corner."""
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    patched = HOME_MARK in src
    if check:
        print("cli.mjs home-button: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("cli.mjs home-button: already patched")
        return 0
    if src.count(HOME_URL_ANCHOR) != 1:
        print(f"anchor count {src.count(HOME_URL_ANCHOR)} != 1 for the home-button url anchor "
              "(run patch_cli first)")
        return 1
    if src.count(HOME_FRAME_ANCHOR) != 1:
        print(f"anchor count {src.count(HOME_FRAME_ANCHOR)} != 1 for the home-button frame anchor")
        return 1
    out = (src.replace(HOME_URL_ANCHOR, HOME_URL_ANCHOR + _home_url_block())
              .replace(HOME_FRAME_ANCHOR, FRAME_INSERT))
    open(CLI, "w", encoding="utf-8", newline="\n").write(out)
    print(f"patched home-button html in {CLI}")
    return 0


HOME_BTN_MARK = "/* patched: Podium home button, top-left of .frame in its own reserved strip */"
# 0.1.84's .frame is no longer position:relative, so the button's absolute position needs it here.
# Reserving a top strip (padding + border-box) puts the button in space the iframe never occupies,
# instead of floating over whatever the artifact renders at its own top-left corner.
HOME_STRIP = 40
HOME_BTN_CSS = HOME_BTN_MARK + "\n.frame {\n  position: relative;\n  padding-top: " + str(HOME_STRIP) + "px;\n" + """  box-sizing: border-box;
}
.podium-home-btn {
  position: absolute;
  top: 6px;
  left: 8px;
  z-index: 5;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px 7px 9px;
  border-radius: 999px;
  background: #ffffff;
  color: #1a1a1a;
  border: 1px solid #e4e2dd;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .12);
  font: 600 12px/1 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  text-decoration: none;
  cursor: pointer;
}
.podium-home-btn:hover {
  border-color: #b45309;
  color: #b45309;
}
@media (prefers-color-scheme: dark) {
  .podium-home-btn {
    background: #212121;
    color: #eeeeee;
    border-color: #333333;
    box-shadow: 0 1px 4px rgba(0, 0, 0, .4);
  }
  .podium-home-btn:hover {
    border-color: #d97a2b;
    color: #d97a2b;
  }
}
@media (max-width: 480px) {
  .podium-home-btn span {
    display: none;
  }
  .podium-home-btn {
    padding: 8px;
  }
}
"""


# The strip is for pages that need the injected pill. A page carrying the podium-own-home marker
# draws Home in its own bar, so it gets the frame back - one row of screen, on every load.
HOME_BTN_MARK2 = "/* patched: no reserved strip for a page that owns its own Home button */"
HOME_BTN_CSS2 = HOME_BTN_MARK2 + "\n.frame.no-home-strip {\n  padding-top: 0;\n}\n"


def patch_home_css(check: bool) -> int:
    if not os.path.exists(CSS):
        print(f"not found: {CSS}")
        return 1
    src = open(CSS, encoding="utf-8").read()
    patched = HOME_BTN_MARK in src and HOME_BTN_MARK2 in src
    if check:
        print("chrome.css home-button: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("chrome.css home-button: already patched")
        return 0
    out = src.rstrip("\n")
    if HOME_BTN_MARK not in src:
        out += "\n\n" + HOME_BTN_CSS
    if HOME_BTN_MARK2 not in src:
        out += "\n\n" + HOME_BTN_CSS2
    open(CSS, "w", encoding="utf-8", newline="\n").write(out)
    print(f"patched home-button css in {CSS}")
    return 0


TOPNAV_OLD = 'sandbox="allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads"'
TOPNAV_ADD = "allow-top-navigation-by-user-activation"
TOPNAV_NEW = TOPNAV_OLD[:-1] + " " + TOPNAV_ADD + '"'


def patch_top_navigation(check: bool) -> int:
    """A `target="_top"` link inside the artifact iframe does nothing at all, with no console
    error, unless the iframe's own sandbox allows it - the default sandbox has neither
    `allow-top-navigation` nor `allow-top-navigation-by-user-activation`, so home.html's row links
    silently went nowhere (measured 2026-09-10: click, no navigation, no error). The
    `-by-user-activation` variant is the narrower grant: it only fires from a real click, never a
    script-triggered redirect, so an artifact page still cannot hijack the tab on its own.

    The anchor is the whole attribute, because the whiteboard frame carries its own, different
    `sandbox="allow-scripts allow-popups"`.

    No JS-comment idempotency mark here on purpose - this text lands inside the HTML template
    literal's OUTPUT (the iframe tag itself), not in JS source, so a `/* ... */` marker would show
    up as bogus HTML rather than a comment. TOPNAV_ADD's own presence is the idempotency check."""
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    patched = TOPNAV_ADD in src
    if check:
        print("cli.mjs top-navigation: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("cli.mjs top-navigation: already patched")
        return 0
    if src.count(TOPNAV_OLD) != 1:
        print(f"anchor count {src.count(TOPNAV_OLD)} != 1 for the iframe sandbox attribute")
        return 1
    out = src.replace(TOPNAV_OLD, TOPNAV_NEW, 1)
    open(CLI, "w", encoding="utf-8", newline="\n").write(out)
    print(f"patched top-navigation sandbox in {CLI}")
    return 0


def patch_home(check: bool) -> int:
    r1 = patch_home_html(check)
    r2 = patch_home_css(check)
    r3 = patch_top_navigation(check)
    return r1 or r2 or r3


ANNOTATE_MARK = "/* patched: Annotate defaults off on Home/Board so row clicks navigate */"
ANNOTATE_JS_OLD = "let annotation = true;\n"
ANNOTATE_JS_NEW = (
    ANNOTATE_MARK + "\n"
    'let annotation = annotationSwitch.getAttribute("aria-pressed") === "true";\n'
)
ANNOTATE_HTML_OLD = 'id="annotation" type="button" aria-pressed="true" title='
ANNOTATE_HTML_NEW = (
    'id="annotation" type="button" '
    'aria-pressed="${podiumOwnHome ? "false" : "true"}" title='
)


def patch_annotate_default(check: bool) -> int:
    """Ryan, 2026-09-17: Home (and any page with podium-own-home, including the board) is a
    clickable dashboard. Annotate-on captures `<a class=row>` clicks so nothing navigates until
    he toggles it off. Design-review pages keep Annotate on. Chrome reads the button's
    aria-pressed at boot, and createChromeHtml sets that from podiumOwnHome (needs patch_home_html)."""
    if not os.path.exists(CLIENT) or not os.path.exists(CLI):
        print("not found: chrome-client.js or cli.mjs")
        return 1
    js = open(CLIENT, encoding="utf-8").read()
    html = open(CLI, encoding="utf-8").read()
    js_ok = ANNOTATE_MARK in js
    html_ok = ANNOTATE_HTML_NEW in html
    if check:
        print("annotate-default js: " + ("patched" if js_ok else "not patched"))
        print("annotate-default html: " + ("patched" if html_ok else "not patched"))
        return 0 if js_ok and html_ok else 1
    if js_ok and html_ok:
        print("annotate-default: already patched")
        return 0
    if not js_ok:
        if js.count(ANNOTATE_JS_OLD) != 1:
            print(f"anchor count {js.count(ANNOTATE_JS_OLD)} != 1 for let annotation = true")
            return 1
        js = js.replace(ANNOTATE_JS_OLD, ANNOTATE_JS_NEW)
        open(CLIENT, "w", encoding="utf-8", newline="\n").write(js)
        print(f"patched annotate-default js in {CLIENT}")
    if not html_ok:
        if html.count(ANNOTATE_HTML_OLD) != 1:
            print(f"anchor count {html.count(ANNOTATE_HTML_OLD)} != 1 for aria-pressed=true")
            return 1
        html = html.replace(ANNOTATE_HTML_OLD, ANNOTATE_HTML_NEW)
        open(CLI, "w", encoding="utf-8", newline="\n").write(html)
        print(f"patched annotate-default html in {CLI}")
    return 0


PANEL_MARK = "/* patched: Conversation column ~25% narrower per Ryan's feedback */"
PANEL_VAR_RE = re.compile(r"--panel-w:\s*(\d+)px")
PANEL_SHRINK = 0.75          # Ryan, 2026-09-10: "a little smaller" -> about a quarter narrower


def patch_panel_width(check: bool) -> int:
    """The Conversation column's width lives in one CSS custom property (--panel-w), used by both
    `.layout`'s grid-template-columns and `.panel`'s own width - so overriding just the variable
    (the same append-a-later-:root-block trick patch_css uses for colors) resizes the column
    without touching the phone layout at all: the <860px media query already sets `.panel { width:
    100% }` and never reads --panel-w, so it is unaffected by this patch."""
    if not os.path.exists(CSS):
        print(f"not found: {CSS}")
        return 1
    src = open(CSS, encoding="utf-8").read()
    patched = PANEL_MARK in src
    if check:
        print("chrome.css panel-width: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("chrome.css panel-width: already patched")
        return 0
    m = PANEL_VAR_RE.search(src)
    if not m:
        print("--panel-w not found in chrome.css; refusing to patch")
        return 1
    old = int(m.group(1))
    new = round(old * PANEL_SHRINK)
    out = (src.rstrip("\n") + "\n\n" + PANEL_MARK + "\n:root {\n  --panel-w: "
           + str(new) + "px;\n}\n")
    open(CSS, "w", encoding="utf-8", newline="\n").write(out)
    print(f"patched panel width in {CSS}: {old}px -> {new}px")
    return 0


BRASS_MARK = "/* patched: chrome accent is learn-page brass, not burnt orange */"
BRASS_CSS = BRASS_MARK + "\n" + """:root {
  --bg: #faf8f3;
  --bg-panel: #faf8f3;
  --bg-bar: #faf8f3;
  --bg-elevated: #fdf7e6;
  --accent: #f4c95d;
  --accent-hover: #e0b84a;
  --accent-ink: #17130a;
}
/* 0.1.84 also uses the accent as TEXT (Attach images, links in replies, Take over, unread summary);
   brass on cream is unreadable there, so those take a dark brass. */
.chat-attach,
.chat-attachment-chip button,
.chat-md a,
.handoff-takeover,
.warning-chip.status-unverified,
.panel-summary.is-accent {
  color: #8a6508;
}
"""


def patch_brass_accent(check: bool) -> int:
    """Ryan, 2026-09-17: Conversation column Send was burnt orange against a cream learn page.
    Match the learn-page brass (#f4c95d on #faf8f3)."""
    if not os.path.exists(CSS):
        print(f"not found: {CSS}")
        return 1
    src = open(CSS, encoding="utf-8").read()
    patched = BRASS_MARK in src
    if check:
        print("chrome.css brass: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("chrome.css brass: already patched")
        return 0
    open(CSS, "w", encoding="utf-8", newline="\n").write(src.rstrip("\n") + "\n\n" + BRASS_CSS)
    print(f"patched brass accent in {CSS}")
    return 0


FLUSH_MARK = "/* patched: a queuePrompt from the artifact auto-POSTs */"
# enqueuePrompt has three callers: this iframe message, the layout-warnings queue and the whiteboard
# edit (both pass a FeedbackPreparation and are meant to ride along with the NEXT send). Only the
# iframe message auto-flushes, so the patch sits in the message handler, not in enqueuePrompt.
FLUSH_OLD = (
    '  if (msg.type === "lavish:queuePrompt") {\n'
    "    enqueuePrompt(msg.prompt);\n"
)
FLUSH_NEW = (
    '  if (msg.type === "lavish:queuePrompt") {\n'
    "    " + FLUSH_MARK + "\n"
    "    if (enqueuePrompt(msg.prompt)) sendQueued(false);\n"
)


def patch_auto_flush_queue(check: bool) -> int:
    """Ryan, 2026-09-17: Send these on the CS 549 slope page queued locally and never POSTed.
    A prompt the artifact queues now goes straight out. (The second half of the old patch - retry
    sendQueued while the agent is working - is gone: 0.1.84's sendQueued has no presence gate.)
    Upstream's own route to the same end is opt-in per page, window.lavish.sendQueuedPrompts()."""
    if not os.path.exists(CLIENT):
        print(f"not found: {CLIENT}")
        return 1
    src = open(CLIENT, encoding="utf-8").read()
    patched = FLUSH_MARK in src
    if check:
        print("chrome-client.js auto-flush: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("chrome-client.js auto-flush: already patched")
        return 0
    if src.count(FLUSH_OLD) != 1:
        print(f"anchor count {src.count(FLUSH_OLD)} != 1 for the lavish:queuePrompt handler")
        return 1
    open(CLIENT, "w", encoding="utf-8", newline="\n").write(src.replace(FLUSH_OLD, FLUSH_NEW))
    print(f"patched auto-flush in {CLIENT}")
    return 0


ASSET_NOCACHE_MARK = 'res.set("Cache-Control", "no-cache").type("application/javascript")'
# The bundler numbers its imports (readFile5 here, readFile2 in 0.1.32), so match it by shape.
ASSET_JS_RE = re.compile(
    r'      res\.type\("application/javascript"\)\.send\(await (readFile\d*)\(chromeClientUrl, "utf8"\)\);')
ASSET_JS_SUB = (r'      res.set("Cache-Control", "no-cache").type("application/javascript")'
                r'.send(await \1(chromeClientUrl, "utf8"));')
ASSET_CSS_RE = re.compile(
    r'      res\.type\("text/css"\)\.send\(await (readFile\d*)\(chromeCssUrl, "utf8"\)\);')
ASSET_CSS_SUB = (r'      res.set("Cache-Control", "no-cache").type("text/css")'
                 r'.send(await \1(chromeCssUrl, "utf8"));')


def patch_chrome_asset_nocache(check: bool) -> int:
    """chrome-client.js and chrome.css were cached, so a patch on disk never reached the tab."""
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    patched = ASSET_NOCACHE_MARK in src
    if check:
        print("cli.mjs chrome-asset-nocache: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("cli.mjs chrome-asset-nocache: already patched")
        return 0
    if len(ASSET_JS_RE.findall(src)) != 1 or len(ASSET_CSS_RE.findall(src)) != 1:
        print("chrome-asset-nocache anchors missing")
        return 1
    src = ASSET_JS_RE.sub(ASSET_JS_SUB, src)
    src = ASSET_CSS_RE.sub(ASSET_CSS_SUB, src)
    open(CLI, "w", encoding="utf-8", newline="\n").write(src)
    print(f"patched chrome-asset-nocache in {CLI}")
    return 0


MIC_MARK = "// patched: learn voice bridge"
MIC_ANCHOR = ('const frame = /** @type {HTMLIFrameElement} */ '
              '(document.getElementById("artifact"));\n')
MIC_NEW = MIC_ANCHOR + (
    MIC_MARK + " (the relay itself lives in the learn skill: tools/tutor_server.py serves\n"
    "// assets/voice-bridge.js, so this stub never changes when the relay does)\n"
    'window.addEventListener("message", (ev) => {\n'
    "  const d = ev.data;\n"
    '  if (!d || typeof d !== "object" || !d.learnVoice || ev.source !== frame.contentWindow) return;\n'
    "  if (window.__learnVoiceRelay) { window.__learnVoiceRelay(ev); return; }\n"
    "  if (window.__learnVoiceLoading) return;\n"
    "  window.__learnVoiceLoading = true;\n"
    '  const s = document.createElement("script");\n'
    '  s.src = "http://127.0.0.1:8792/voice-bridge.js?t=" + Date.now();\n'
    "  s.onload = () => { window.__learnVoiceLoading = false;\n"
    "    if (window.__learnVoiceRelay) window.__learnVoiceRelay(ev); };\n"
    "  s.onerror = () => { window.__learnVoiceLoading = false; };\n"
    "  document.head.appendChild(s);\n"
    "});\n"
)


def patch_mic_permission(check: bool) -> int:
    """Ryan, 2026-09-30: talk to the learn tutor by voice. The artifact iframe is sandboxed with
    an opaque origin, so getUserMedia and SpeechRecognition cannot run inside it: measured in the
    real Bridge frame, getUserMedia throws "Invalid security origin" even with
    allow="microphone" on the iframe. So the frame asks THIS page (a normal origin) to listen,
    over postMessage. The stub loads the relay script from the learn tutor server on first
    contact (script tags are not CORS-gated; the URL is hard-coded, never taken from the frame).
    chrome-client.js is read per request, so no Bridge restart - an open tab needs one reload."""
    if not os.path.exists(CLIENT):
        print(f"not found: {CLIENT}")
        return 1
    src = open(CLIENT, encoding="utf-8").read()
    patched = MIC_MARK in src
    if check:
        print("chrome-client.js mic-permission: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("chrome-client.js mic-permission: already patched")
        return 0
    if src.count(MIC_ANCHOR) != 1:
        print(f"anchor count {src.count(MIC_ANCHOR)} != 1 for the artifact frame constant")
        return 1
    open(CLIENT, "w", encoding="utf-8", newline="\n").write(src.replace(MIC_ANCHOR, MIC_NEW))
    print(f"patched mic-permission in {CLIENT}")
    return 0


LINKS_SEL_OLD = '"button,input,select,textarea,option,optgroup,label,summary,'
LINKS_SEL_NEW = '"a[href],button,input,select,textarea,option,optgroup,label,summary,'


def patch_links_open(check: bool) -> int:
    """Ryan, 2026-09-22: "This YouTube link doesn't work" on a learn page. Annotate is on by default
    and its capture-phase click handler preventDefault()s every click that is not a "native
    interactive control" - a list with no `a`, so a link click opened an annotation card instead of
    the link (measured by clicking the link in a real Chrome against the live Bridge page). So
    links count as interactive. 0.1.83 added Ctrl/Cmd-click to follow a link in annotate mode, but
    a plain click still annotates, so this stays.

    The second half of the old patch (allow-popups-to-escape-sandbox on the iframe, so a popup does
    not inherit the opaque origin and land on chrome-error://) is dropped: 0.1.84's own sandbox
    already carries it."""
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    sel_ok = LINKS_SEL_NEW in src
    if check:
        print("links-open selector: " + ("patched" if sel_ok else "not patched"))
        return 0 if sel_ok else 1
    if sel_ok:
        print("links-open: already patched")
        return 0
    if src.count(LINKS_SEL_OLD) != 1:
        print(f"anchor count {src.count(LINKS_SEL_OLD)} != 1 for the interactive selector")
        return 1
    open(CLI, "w", encoding="utf-8", newline="\n").write(src.replace(LINKS_SEL_OLD, LINKS_SEL_NEW))
    print(f"patched links-open in {CLI}")
    return 0


# Ryan, 2026-09-23: "get rid of any mention of lavish. It should all be bridge" - flagged the
# presence-banner text verbatim ("...ask your agent to poll for updates from Lavish."). These are
# the human-visible "Lavish" strings in the browser chrome: the server-rendered template and boot
# failsafe in cli.mjs (createChromeHtml, CHROME_BOOT_FAILSAFE_JS) and the client-side copy in
# chrome-client.js. Every other "lavish" hit in these files is an identifier - window.lavish,
# data-lavish-*, body.lavish, the lavish-axi CLI/package name, lavish_notes/playbook copy the CLI
# hands an agent - and stays as-is on purpose, since renaming those would break the SDK contract or
# the installed binary name, not just the label a human reads. 0.1.84 has far more error-path copy
# than 0.1.32 did, so the table is (fragment, expected count); the replacement is always the same
# fragment with Lavish -> Bridge, and a count that is off fails the patch rather than guessing.
DEBRAND_CLI = [
    ("Lavish could not finish loading.", 1),
    ("The Lavish editor script did not load.", 1),
    ("Lavish did not answer the check", 1),
    ("Lavish is still not running. Start it again with your agent", 1),
    ("This review is open in another Lavish tab.", 1),
    ("The Lavish server this page was connected to", 1),
    ("poll for updates from Lavish.", 1),
    ("not part of Lavish. Publishing", 1),
    ("The Lavish annotation SDK is not included.", 1),
    ("Lavish is waiting for fonts and final geometry", 1),
]
DEBRAND_CLIENT = [
    ("Keep this tab open while Lavish catches up", 1),
    ("Check that Lavish is running, then click Send to Agent", 1),
    ("Check that Lavish is running, then click Send & End", 1),
    ('"Lavish did not answer."', 1),
    ("Lavish did not answer the check", 1),
    ("Lavish was updated.", 1),
    ("Lavish was restarted to pick up a local build.", 1),
    ("Lavish was stopped.", 1),
    ("The Lavish server this page was connected to", 1),
    ("so Lavish could not keep it open", 1),
    ("Lavish is waiting for fonts and final geometry", 1),
    ("site id Lavish can use", 1),
    ("Lavish will generate one when you publish", 1),
    ("and Lavish does not store it", 3),
    ("and Lavish could not read the whole result back", 1),
    ("so Lavish cannot show the address", 1),
    ("Lavish could not load this artifact.", 2),
    ("The Lavish server did not answer", 1),
    ("Lavish is still not answering.", 1),
    ("Lavish loads an artifact in one tab at a time.", 1),
    ("Lavish is not running.", 2),
    ("The Lavish server restarted and did not come back.", 1),
    ("Lavish is still not running.", 2),
]


def _debrand(src, table):
    """Returns (new_src, error). Idempotent: a fragment already debranded is skipped."""
    for old, count in table:
        new = old.replace("Lavish", "Bridge")
        if src.count(old) == 0 and src.count(new) >= count:
            continue
        if src.count(old) != count:
            return None, f"debrand anchor count {src.count(old)} != {count} for: {old[:60]}"
        src = src.replace(old, new)
    return src, None


def patch_debrand_visible_text(check: bool) -> int:
    if not os.path.exists(CLI) or not os.path.exists(CLIENT):
        print("not found: cli.mjs or chrome-client.js")
        return 1
    cli_src = open(CLI, encoding="utf-8").read()
    client_src = open(CLIENT, encoding="utf-8").read()
    cli_ok = all(old not in cli_src for old, _ in DEBRAND_CLI) and NEW_BRAND_FALLBACK in cli_src
    client_ok = all(old not in client_src for old, _ in DEBRAND_CLIENT)
    if check:
        print("cli.mjs debrand: " + ("patched" if cli_ok else "not patched"))
        print("chrome-client.js debrand: " + ("patched" if client_ok else "not patched"))
        return 0 if cli_ok and client_ok else 1
    if cli_ok and client_ok:
        print("debrand: already patched")
        return 0
    if not cli_ok:
        out, err = _debrand(cli_src, DEBRAND_CLI)
        if err:
            print(err.replace("debrand anchor", "debrand anchor (cli.mjs)"))
            return 1
        if OLD_BRAND not in out:
            print("debrand anchor count 0 != 1 for cli.mjs: brand (run patch_cli first)")
            return 1
        out = out.replace(OLD_BRAND, NEW_BRAND_FALLBACK)
        open(CLI, "w", encoding="utf-8", newline="\n").write(out)
        print(f"patched debrand text in {CLI}")
    if not client_ok:
        out, err = _debrand(client_src, DEBRAND_CLIENT)
        if err:
            print(err.replace("debrand anchor", "debrand anchor (chrome-client.js)"))
            return 1
        open(CLIENT, "w", encoding="utf-8", newline="\n").write(out)
        print(f"patched debrand text in {CLIENT}")
    return 0


NEW_BRAND_FALLBACK = '<span class="brand-mark">Bridge</span><span class="brand-support">Editor</span>'


def apply_own(check: bool) -> list:
    """Every sub-patch in this file, without the chained scripts (the tests run this)."""
    return [
        patch_cli(check),
        patch_css(check),
        patch_gate(check),
        patch_cache_control(check),
        patch_sending(check),
        patch_home(check),
        patch_panel_width(check),
        patch_annotate_default(check),
        patch_brass_accent(check),
        patch_auto_flush_queue(check),
        patch_chrome_asset_nocache(check),
        patch_links_open(check),
        patch_debrand_visible_text(check),
        patch_mic_permission(check),
    ]


def main() -> int:
    check = "--check" in sys.argv
    rs = apply_own(check)
    import patch_lavish_chat_status          # chat status line + bubble look; needs the patches above
    rs.append(patch_lavish_chat_status.main())
    import patch_lavish_atomic_state         # atomic state.json write, tolerant read, ended-session cap
    rs.append(patch_lavish_atomic_state.main())
    # patch_lavish_server_leak (SSE close-before-register leak) is retired: 0.1.84 fixed it upstream.
    import patch_lavish_state_cache          # state.json kept in memory; layers on the atomic-state patch
    rs.append(patch_lavish_state_cache.main())
    import patch_lavish_annotate_at_cursor   # empty-space Annotate click anchors the card at the cursor
    rs.append(patch_lavish_annotate_at_cursor.main())
    import patch_lavish_chat_send            # live "Agent got it - working" status + timer, instant snapshot fallback
    rs.append(patch_lavish_chat_send.main())
    return int(any(rs))


if __name__ == "__main__":
    sys.exit(main())
