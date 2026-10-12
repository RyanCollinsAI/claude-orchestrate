"""Patch lavish-axi so an Annotate click on empty space puts the card at the cursor.

Ryan, 2026-10-01: with Annotate on, clicking where there is no specific element opened the
annotation card at the bottom left of the frame.

Root cause: the in-frame click handler in cli.mjs (createArtifactSdk) always calls
showAnnotationCard(event.target), and the card is placed at target.getBoundingClientRect()
(left edge, bottom + 8). On empty space the target is <html> or <body> (or a page-wide wrapper),
whose rect is the whole page. Its left is 0 and its bottom is below the viewport, so the clamp
pins the card to the bottom left corner.

Fix: when the target is <html>/<body>, or its rect covers most of the viewport, anchor the card
at the click point instead. The point is stored as PAGE coordinates (pageX/pageY) and the card
is re-placed on scroll, so it stays put on the content. No outline is drawn on the huge element.
Element clicks and text-range selections keep their old behaviour.

The SDK is injected into every page with Function.toString(), so the edit has no escaping
concerns. It lives in cli.mjs, which is loaded once per server process, but the SDK text is
read on each page request: apply it, then reload the tab. No Bridge restart is needed IF the
served SDK is built per request; see the report for the measured answer.

    py patch_lavish_annotate_at_cursor.py            # patch, or say "already patched"
    py patch_lavish_annotate_at_cursor.py --check    # exit 0 if patched, 1 if not

LAVISH_DIST overrides the target dist folder (used to test on a throwaway copy).
"""
import os
import sys

DIST = os.environ.get("LAVISH_DIST") or os.path.expanduser(r"~\AppData\Roaming\npm\node_modules\lavish-axi\dist")
CLI = os.path.join(DIST, "cli.mjs")

MARK = "/* patched: annotate at cursor */"

CLOSE_ANCHOR = "  function closeCard() {\n    activeCardContext = null;\n"
CLOSE_NEW = (
    "  " + MARK + "\n"
    "  let annotatePoint = null;\n"
    "  let annotateCard = null;\n"
    "  function placeAnnotateCard() {\n"
    "    if (!annotatePoint || !annotateCard || !annotateCard.isConnected) return;\n"
    "    const x = annotatePoint.pageX - window.scrollX;\n"
    "    const y = annotatePoint.pageY - window.scrollY;\n"
    "    annotateCard.style.left = Math.min(Math.max(12, x), window.innerWidth - annotateCard.offsetWidth - 12) + \"px\";\n"
    "    annotateCard.style.top = Math.min(Math.max(12, y), window.innerHeight - annotateCard.offsetHeight - 12) + \"px\";\n"
    "  }\n"
    "  window.addEventListener(\"scroll\", placeAnnotateCard, { passive: true });\n"
    "  function isEmptySpaceTarget(el, rect) {\n"
    "    if (!el || el === document.body || el === document.documentElement) return true;\n"
    "    return rect.width >= window.innerWidth * 0.9 && rect.height >= window.innerHeight * 0.6;\n"
    "  }\n"
    "  function closeCard() {\n"
    "    activeCardContext = null;\n"
    "    annotatePoint = null;\n"
    "    annotateCard = null;\n"
)

HILITE_ANCHOR = (
    "    if (options2.range) {\n"
    "      highlightTextRange(options2.range);\n"
    "    } else {\n"
    "      anchor = annotationTargetEl(target);\n"
    "      selected = anchor;\n"
    "      highlightElement(selected);\n"
    "    }\n"
)
HILITE_NEW = (
    "    const pointMode = !options2.range && options2.point && isEmptySpaceTarget(target, target.getBoundingClientRect());\n"
    "    if (options2.range) {\n"
    "      highlightTextRange(options2.range);\n"
    "    } else if (!pointMode) {\n"
    "      anchor = annotationTargetEl(target);\n"
    "      selected = anchor;\n"
    "      highlightElement(selected);\n"
    "    }\n"
)

PLACE_ANCHOR = (
    "      const left = Math.min(Math.max(12, rect.left), window.innerWidth - card.offsetWidth - 12);\n"
    "      const top = Math.min(Math.max(12, rect.bottom + 8), window.innerHeight - card.offsetHeight - 12);\n"
    "      card.style.left = left + \"px\";\n"
    "      card.style.top = top + \"px\";\n"
)
PLACE_NEW = (
    "      if (pointMode) {\n"
    "        annotatePoint = options2.point;\n"
    "        annotateCard = card;\n"
    "        placeAnnotateCard();\n"
    "        return;\n"
    "      }\n"
    "      const left = Math.min(Math.max(12, rect.left), window.innerWidth - card.offsetWidth - 12);\n"
    "      const top = Math.min(Math.max(12, rect.bottom + 8), window.innerHeight - card.offsetHeight - 12);\n"
    "      card.style.left = left + \"px\";\n"
    "      card.style.top = top + \"px\";\n"
)

CLICK_ANCHOR = "      showAnnotationCard(event.target);\n"
CLICK_NEW = "      showAnnotationCard(event.target, { point: { pageX: event.pageX, pageY: event.pageY } });\n"

EDITS = [
    (CLOSE_ANCHOR, CLOSE_NEW),
    (HILITE_ANCHOR, HILITE_NEW),
    (PLACE_ANCHOR, PLACE_NEW),
    (CLICK_ANCHOR, CLICK_NEW),
]


def main():
    check = "--check" in sys.argv
    if not os.path.exists(CLI):
        print(f"not found: {CLI}")
        return 1
    src = open(CLI, encoding="utf-8").read()
    patched = MARK in src
    if check:
        print("cli.mjs annotate-at-cursor: " + ("patched" if patched else "not patched"))
        return 0 if patched else 1
    if patched:
        print("cli.mjs annotate-at-cursor: already patched")
        return 0
    for anchor, _ in EDITS:
        n = src.count(anchor)
        if n != 1:
            print(f"cli.mjs annotate-at-cursor: anchor count {n} != 1 for {anchor[:70]!r} "
                  "(this lavish-axi version moved the code)")
            return 1
    for anchor, new in EDITS:
        src = src.replace(anchor, new)
    if MARK not in src:
        print("cli.mjs annotate-at-cursor: marker did not land; refusing to write")
        return 1
    open(CLI, "w", encoding="utf-8", newline="\n").write(src)
    print(f"patched annotate-at-cursor in {CLI}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
