"""Runs each script-style test (see conftest.py) in its own process, so it keeps its own env."""
import os, subprocess, sys

import pytest

from conftest import SCRIPT_TESTS

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.parametrize("script", SCRIPT_TESTS)
def test_script_passes(script):
    r = subprocess.run([sys.executable, os.path.join(HERE, script)], cwd=HERE, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600)
    assert r.returncode == 0, f"{script} exit {r.returncode}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}"
