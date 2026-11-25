import pytest

from durations import parse_duration


@pytest.mark.parametrize("text,seconds", [
    ("1h30m", 5400), ("45s", 45), ("2h", 7200), ("1h2m3s", 3723), ("1h 30m", 5400), ("90m", 5400),
    ("0s", 0), (" 3m ", 180),
])
def test_valid(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "   ", "10", "5x", "1h1h", "-5m", "1m1h", "h", "1.5h", "1hour"])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_duration(text)
