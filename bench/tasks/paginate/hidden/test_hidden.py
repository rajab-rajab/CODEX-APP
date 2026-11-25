import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pages import page_count, paginate  # noqa: E402


def _text(path):
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_visible_tests_unchanged():
    assert _text("tests/test_pages.py") == _text(Path(__file__).with_name("test_pages.orig"))


def test_pages():
    items = list(range(1, 26))
    assert paginate(items, 1, 10) == list(range(1, 11))
    assert paginate(items, 3, 10) == [21, 22, 23, 24, 25]
    assert paginate(items, 4, 10) == []
    assert paginate(items, 1, 30) == items
    assert page_count(items, 10) == 3 and page_count(items, 5) == 5 and page_count(items, 30) == 1


def test_bad_page_or_size():
    for page, size in ((0, 10), (-1, 10), (1, 0)):
        with pytest.raises(ValueError):
            paginate([1, 2, 3], page, size)
