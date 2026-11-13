import pytest

from slices import last_n


def test_last_two():
    assert last_n([1, 2, 3, 4], 2) == [3, 4]


def test_longer_than_list():
    assert last_n([1, 2], 5) == [1, 2]


def test_zero():
    assert last_n([1, 2, 3], 0) == []


def test_negative():
    with pytest.raises(ValueError):
        last_n([1], -1)
