"""Throwaway test for checking /pr fix end to end."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_app.demo_calc import add  # noqa: E402


def test_add():
    assert add(2, 3) == 5
