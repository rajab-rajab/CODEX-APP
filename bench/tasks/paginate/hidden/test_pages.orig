import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pages import page_count, paginate  # noqa: E402

ITEMS = list(range(1, 26))  # 25 items


def test_first_and_second_page():
    assert paginate(ITEMS, 1, 10) == list(range(1, 11))
    assert paginate(ITEMS, 2, 10) == list(range(11, 21))


def test_last_page_is_short():
    assert paginate(ITEMS, 3, 10) == [21, 22, 23, 24, 25]


def test_page_count_rounds_up():
    assert page_count(ITEMS, 10) == 3
    assert page_count([], 10) == 0
