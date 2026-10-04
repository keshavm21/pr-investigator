"""Input validation helpers shared by the routes."""

from __future__ import annotations

from app.web import HTTPError

# Maps public sort keys to trusted column names. Never interpolate anything else into SQL.
SORTABLE_COLUMNS = {"name": "name", "price": "price", "newest": "created_at"}


def validate_sort_column(key: str) -> str:
    try:
        return SORTABLE_COLUMNS[key]
    except KeyError:
        raise HTTPError(400, f"cannot sort by {key!r}") from None


def parse_positive_int(raw: str | None, default: int, maximum: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise HTTPError(400, "expected an integer") from None
    if value < 1:
        raise HTTPError(400, "expected a positive integer")
    return min(value, maximum)


def parse_offset(raw: str | None) -> int:
    if raw is None:
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise HTTPError(400, "expected an integer") from None
    if value < 0:
        raise HTTPError(400, "offset must not be negative")
    return value
