# These test files are plain scripts: every check runs at import and the file ends in
# sys.exit(1) on a failure. Imported into one pytest process they share env and module state with
# the other tests, fail, and the exit aborts collection of the whole suite. test_script_tests.py
# runs each one in its own process instead, the way its docstring says to run it.
SCRIPT_TESTS = [
    "test_board_done.py",
    "test_board_autopick.py",
    "test_board_watch_empty_poll.py",
    "test_podium_home.py",
    "test_podium_watch_listener.py",
    "test_podium_watch_standdown.py",
]

collect_ignore = list(SCRIPT_TESTS)
