"""Sales reports for staff."""

from __future__ import annotations

from typing import Any

from app.db import Database
from app.validators import validate_sort_column


def top_items(db: Database, sort_key: str, limit: int) -> list[dict[str, Any]]:
    column = validate_sort_column(sort_key)
    rows = db.query(f"SELECT id, name, price FROM items ORDER BY {column} DESC LIMIT ?", (limit,))
    return [dict(row) for row in rows]
