import pytest

from instagram.parser import normalize_username, parse_story_permalink


def test_normalize_username_from_plain_username():
    assert normalize_username("NASA") == "nasa"


def test_normalize_username_from_at_username():
    assert normalize_username("@nasa") == "nasa"


def test_normalize_username_from_url():
    assert normalize_username("https://www.instagram.com/nasa/") == "nasa"


def test_reject_reserved_path():
    with pytest.raises(ValueError):
        normalize_username("https://instagram.com/stories/")


def test_parse_story_permalink():
    parsed = parse_story_permalink(
        "https://www.instagram.com/stories/SantosWeslleyApi/3993504863237517245/?utm_source=test#x"
    )
    assert parsed.username == "santosweslleyapi"
    assert parsed.seed_story_id == "3993504863237517245"
    assert parsed.canonical_permalink == (
        "https://www.instagram.com/stories/santosweslleyapi/3993504863237517245/"
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/stories/nasa/123/",
        "https://www.instagram.com/nasa/",
        "https://www.instagram.com/stories/nasa/not-number/",
        "https://www.instagram.com/stories//123/",
    ],
)
def test_parse_story_permalink_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_story_permalink(value)
