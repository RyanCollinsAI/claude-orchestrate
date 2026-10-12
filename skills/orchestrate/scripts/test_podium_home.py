"""Regression test for podium_home.py: the row-building logic behind `board/home.html`.

Points STATE/OWNERS at temp files and BOARD at an isolated dir (ORCH_BOARD_DIR, same trick
test_board_notes.py uses) so it never touches the real board or ~/.lavish-axi/state.json.

    py test_podium_home.py
"""
import datetime, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

failures = []


def check(label, cond):
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


def iso(minutes_ago=0):
    t = datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)
    return t.replace(microsecond=0).isoformat()


with tempfile.TemporaryDirectory() as tmp:
    os.environ["ORCH_BOARD_DIR"] = tmp

    import podium_home as PH   # imported after ORCH_BOARD_DIR is set, so B.BOARD picks it up

    recent_html = os.path.join(tmp, "recent.html")
    open(recent_html, "w", encoding="utf-8").write("<html><head><title>  A  Recent   Page </title>"
                                                    "</head><body></body></html>")
    stale_html = os.path.join(tmp, "stale.html")
    open(stale_html, "w", encoding="utf-8").write("<html><head><title>Stale</title></head></html>")
    notitle_html = os.path.join(tmp, "notitle.html")
    open(notitle_html, "w", encoding="utf-8").write("<html><body>no title here</body></html>")
    classes_html = os.path.join(tmp, "classes-overview.html")
    open(classes_html, "w", encoding="utf-8").write("<title>Fall Classes</title>")

    PH.STATE = os.path.join(tmp, "lavish-state.json")   # distinct from B.BOARD's own state.json
    json.dump({"sessions": {
        "k1": {"file": recent_html, "status": "open", "updated_at": iso(5),
               "url": "http://127.0.0.1:1/session/k1"},
        "k2": {"file": stale_html, "status": "open", "updated_at": iso(60 * 24 * 30)},   # 30d old
        "k3": {"file": notitle_html, "status": "ended", "updated_at": iso(1)},           # ended
        "k4": {"file": classes_html, "status": "open", "updated_at": iso(2)},
        "k5": {"file": os.path.join(tmp, "board-calm.html"), "status": "open", "updated_at": iso(1)},
    }}, open(PH.STATE, "w", encoding="utf-8"))

    PH.OWNERS = os.path.join(tmp, "podium-owners.json")
    json.dump({recent_html: "learn-recent", classes_html: PH.CLASSES_OWNER},
              open(PH.OWNERS, "w", encoding="utf-8"))

    check("title_of reads and collapses whitespace from <title>",
          PH.title_of(recent_html) == "A Recent Page")
    check("title_of falls back to the filename with no <title>",
          PH.title_of(notitle_html) == "notitle.html")

    sessions = PH.open_sessions()
    check("open_sessions keeps a recent open page",
          os.path.normcase(os.path.abspath(recent_html)) in sessions)
    check("open_sessions drops a page not touched in RECENT_DAYS",
          os.path.normcase(os.path.abspath(stale_html)) not in sessions)
    check("open_sessions drops an ended session",
          os.path.normcase(os.path.abspath(notitle_html)) not in sessions)
    check("open_sessions drops the calm board page like the board itself",
          os.path.normcase(os.path.abspath(os.path.join(tmp, "board-calm.html"))) not in sessions)

    check("classes_path finds the file labeled CLASSES_OWNER in podium-owners.json",
          os.path.normcase(os.path.abspath(PH.classes_path() or "")) ==
          os.path.normcase(os.path.abspath(classes_html)))

    rows, board, classes = PH.build()
    check("the classes page is excluded from the general row list",
          all(r["path"] != classes_html for r in rows))
    check("the general row list keeps the recent page with its owner",
          any(r["owner"] == "learn-recent" for r in rows))
    check("classes_row carries its own title", classes and classes["title"] == "Fall Classes")
    check("board_summary counts nothing on an empty board",
          board == {"open_questions": 0, "show_blocks": 0})

    page = PH._page(rows, board, classes)
    check("the rendered page embeds the recent page's title", "A Recent Page" in page)
    check("the rendered page embeds the classes title", "Fall Classes" in page)
    check("the rendered page never embeds the stale page", "Stale" not in page)
    check("every row navigates the top window, not home.html's own artifact iframe",
          page.count("<a class='row'") == page.count("target='_top'"))
    check("home.html carries podium-own-home so Annotate defaults off",
          "podium-own-home" in page)

    out_rows, out_board, out_classes = PH.render()
    check("render() writes home.html", os.path.exists(PH.HOME))
    check("render() returns the same rows build() would", out_rows == rows)

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nall checks passed")
