"""Make the Lavish Editor top bar show the artifact's own <title> instead of "Lavish Editor".

lavish-axi renders its chrome server-side in dist/cli.mjs (createChromeHtml). Ryan asked
2026-09-04 that Podium stop saying "Lavish EDITOR" at the top. The package has no option
for it, so this patches the installed file in place. Idempotent: run it after every
`npm i -g lavish-axi` update. Restart the server afterwards (`lavish-axi stop`, then open
the page again) - the HTML is built per request but the module is loaded once.

    py patch_lavish_brand.py            # patch, or say "already patched"
    py patch_lavish_brand.py --check    # exit 0 if patched, 1 if not
"""
import os
import re
import sys

CLI = os.path.expanduser(r"~\AppData\Roaming\npm\node_modules\lavish-axi\dist\cli.mjs")
MARK = "/* patched: artifact title in the bar */"

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


def main() -> int:
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    patched = MARK in src
    if "--check" in sys.argv:
        print("patched" if patched else "not patched")
        return 0 if patched else 1
    if patched:
        print("already patched")
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


if __name__ == "__main__":
    sys.exit(main())
