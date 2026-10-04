"""Thin wrapper around sqlite3. Always pass values as parameters, never in the SQL text."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

Params = Sequence[Any]


class Database:
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def query(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Params = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: Params = ()) -> int:
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        return cursor.rowcount


db = Database("inventory.db")
