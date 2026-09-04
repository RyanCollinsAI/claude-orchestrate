"""Make the Lavish Editor chrome look like Podium instead of like Lavish.

lavish-axi renders its chrome server-side in dist/cli.mjs (createChromeHtml) and styles it
from dist/chrome.css. Ryan asked 2026-09-04 that Podium stop saying "Lavish EDITOR" at the
top (patches cli.mjs), and 2026-09-04 that the top bar and the right Conversation sidebar
stop looking like near-black Lavish and read as Podium's own light palette instead (patches
chrome.css, appending an override :root block whose values are lifted from board.py's own
:root vars, keeping board.py's --ask amber as the accent so page and chrome match). The
package has no option for either, so this patches the installed files in place. Idempotent:
run it after every `npm i -g lavish-axi` update. Restart the server afterwards
(`lavish-axi stop`, then open the page again) - the HTML/CSS is read per request but the
cli.mjs module is loaded once.

    py patch_lavish_brand.py            # patch both, or say "already patched"
    py patch_lavish_brand.py --check    # exit 0 if both patched, 1 if not
"""
import os
import re
import sys

CLI = os.path.expanduser(r"~\AppData\Roaming\npm\node_modules\lavish-axi\dist\cli.mjs")
CSS = os.path.join(os.path.dirname(CLI), "chrome.css")
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

ANCHOR = "const { head: pathHead, tail: pathTail } = displayPathParts(session.file);\n"
INSERT = ANCHOR + (
    "  " + MARK + "\n"
    "  const artifactTitle = (() => { try { const m = readFileSync(session.file, \"utf8\")"
    ".slice(0, 8192).match(/<title>([^<]*)<\\/title>/i); return m ? m[1].trim() : \"\"; }"
    " catch { return \"\"; } })();\n"
    "  const artifactTitleHtml = artifactTitle.replace(/&/g, \"&amp;\")"
    ".replace(/</g, \"&lt;\").replace(/>/g, \"&gt;\");\n"
)
OLD_TITLE = "<title>Lavish Editor</title>"
NEW_TITLE = "<title>${artifactTitleHtml || \"Lavish Editor\"}</title>"
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
    for needle in (ANCHOR, OLD_TITLE, OLD_BRAND):
        if src.count(needle) != 1:
            print(f"anchor count {src.count(needle)} != 1 for: {needle[:60]}")
            return 1
    out = src.replace(ANCHOR, INSERT).replace(OLD_TITLE, NEW_TITLE).replace(OLD_BRAND, NEW_BRAND)
    if "readFileSync" not in src.split("createChromeHtml")[0]:
        print("readFileSync is not imported in cli.mjs; refusing to patch")
        return 1
    open(CLI, "w", encoding="utf-8", newline="\n").write(out)
    print(f"patched {CLI}")
    return 0


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


def main() -> int:
    check = "--check" in sys.argv
    r1 = patch_cli(check)
    r2 = patch_css(check)
    return r1 or r2


if __name__ == "__main__":
    sys.exit(main())
