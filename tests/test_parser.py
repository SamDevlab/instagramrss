import pytest

from instagram.parser import normalize_username


def test_normalize_username_from_plain_username():
    assert normalize_username("NASA") == "nasa"


def test_normalize_username_from_at_username():
    assert normalize_username("@nasa") == "nasa"


def test_normalize_username_from_url():
    assert normalize_username("https://www.instagram.com/nasa/") == "nasa"


def test_reject_reserved_path():
    with pytest.raises(ValueError):
        normalize_username("https://instagram.com/stories/")
