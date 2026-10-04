import pytest

from app.validators import parse_positive_int, validate_sort_column
from app.web import HTTPError


def test_sort_column_allowlist() -> None:
    assert validate_sort_column("newest") == "created_at"
    with pytest.raises(HTTPError):
        validate_sort_column("name; DROP TABLE items")


def test_parse_positive_int_caps_at_maximum() -> None:
    assert parse_positive_int("500", default=20, maximum=100) == 100
