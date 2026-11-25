import pytest

from stats import mean, median


def test_odd_and_even():
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 3, 2]) == 2.5
    assert median([5]) == 5
    assert median([1, 2]) == 1.5


def test_empty_raises():
    with pytest.raises(ValueError):
        median([])


def test_input_not_changed():
    values = [3, 1, 2]
    median(values)
    assert values == [3, 1, 2]


def test_mean_still_works():
    assert mean([1, 2, 3]) == 2
