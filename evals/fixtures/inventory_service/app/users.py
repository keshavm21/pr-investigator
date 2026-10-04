"""User lookups."""

from __future__ import annotations

from app.db import Database
from app.models import User


def get_user(db: Database, user_id: int) -> User | None:
    """Return the user, or None if no such user exists."""
    row = db.query_one("SELECT id, email, role, active FROM users WHERE id = ?", (user_id,))
    if row is None:
        return None
    return User(id=row["id"], email=row["email"], role=row["role"], active=bool(row["active"]))
