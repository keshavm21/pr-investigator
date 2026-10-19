"""The structural index (D10): facts per blob in one SQLite file, mapped onto a commit on demand.

Facts are keyed by (blob SHA, parser version). A commit is a map from path to blob SHA, so a
file version is parsed once and reused by every commit and PR that contains it: indexing a PR
head after its base only parses the blobs the PR changed. A ref is indexed on first use, so a
review that never asks a structural question costs nothing.

Only Python files up to MAX_INDEXED_BYTES are indexed (D7). Blob contents come from git
objects, never from the checkout, so the facts always describe the analyzed commit.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pr_investigator.index.python import (
    PARSER_VERSION,
    FileFacts,
    Import,
    Reference,
    Symbol,
    parse_python,
)
from pr_investigator.workspace import Workspace

MAX_INDEXED_BYTES = 1_000_000
_LOOKUP_CHUNK = 500  # stays well under SQLite's limit on query parameters

Ref = Literal["head", "base"]  # the same values as tools.base.Ref; the tools depend on this module

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs (
    blob_sha TEXT NOT NULL, version INTEGER NOT NULL, PRIMARY KEY (blob_sha, version)
);
CREATE TABLE IF NOT EXISTS symbols (
    blob_sha TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL, qualname TEXT NOT NULL,
    kind TEXT NOT NULL, start_line INTEGER NOT NULL, def_line INTEGER NOT NULL,
    header_end_line INTEGER NOT NULL, end_line INTEGER NOT NULL, parent TEXT
);
CREATE INDEX IF NOT EXISTS symbols_by_name ON symbols (name, version);
CREATE INDEX IF NOT EXISTS symbols_by_blob ON symbols (blob_sha, version);
CREATE TABLE IF NOT EXISTS refs (
    blob_sha TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL, line INTEGER NOT NULL,
    kind TEXT NOT NULL, scope TEXT
);
CREATE INDEX IF NOT EXISTS refs_by_name ON refs (name, version);
CREATE INDEX IF NOT EXISTS refs_by_blob ON refs (blob_sha, version);
CREATE TABLE IF NOT EXISTS imports (
    blob_sha TEXT NOT NULL, version INTEGER NOT NULL, line INTEGER NOT NULL,
    level INTEGER NOT NULL, module TEXT NOT NULL, name TEXT, alias TEXT
);
CREATE INDEX IF NOT EXISTS imports_by_blob ON imports (blob_sha, version);
"""

_SYMBOL_COLUMNS = "name, qualname, kind, start_line, def_line, header_end_line, end_line, parent"


@dataclass(frozen=True)
class Snapshot:
    """The Python files of one commit, as the index sees them."""

    rev: str
    files: dict[str, str]  # path -> blob SHA
    too_large: tuple[str, ...]  # Python files skipped for size


class RepoIndex:
    """The index as seen from one review's workspace: its head and base commits."""

    def __init__(self, db_path: Path, workspace: Workspace) -> None:
        self.db_path = db_path
        self.workspace = workspace
        self.parsed_blobs = 0  # blobs this instance parsed (the rest were already stored)
        self._db: sqlite3.Connection | None = None
        self._snapshots: dict[Ref, Snapshot] = {}

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def snapshot(self, ref: Ref) -> Snapshot:
        """The ref's Python files, indexing any blob the store doesn't have yet."""
        if ref not in self._snapshots:
            rev = self.workspace.head_sha if ref == "head" else self.workspace.merge_base_sha
            files: dict[str, str] = {}
            too_large: list[str] = []
            for entry in self.workspace.tree_files(rev):
                if not entry.path.endswith(".py"):
                    continue
                if entry.size > MAX_INDEXED_BYTES:
                    too_large.append(entry.path)
                else:
                    files[entry.path] = entry.blob_sha
            self._index(sorted(set(files.values())))
            db = self._connect()
            table = _table(ref)
            db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute(f"CREATE TEMP TABLE {table} (path TEXT PRIMARY KEY, blob_sha TEXT)")
            db.executemany(f"INSERT INTO {table} VALUES (?, ?)", files.items())
            self._snapshots[ref] = Snapshot(rev=rev, files=files, too_large=tuple(too_large))
        return self._snapshots[ref]

    def definitions(self, ref: Ref, name: str) -> list[tuple[str, Symbol]]:
        """Symbols whose name is `name`, or whose qualified name is or ends with a dotted `name`."""
        last = name.rsplit(".", 1)[-1]
        rows = self._query(
            ref,
            f"SELECT s.path, {_SYMBOL_COLUMNS} FROM {{snap}} s JOIN symbols USING (blob_sha) "
            "WHERE version = ? AND name = ? ORDER BY s.path, start_line, qualname",
            (PARSER_VERSION, last),
        )
        found = [(row[0], Symbol(*row[1:])) for row in rows]
        if "." in name:
            found = [
                (p, s) for p, s in found if s.qualname == name or s.qualname.endswith(f".{name}")
            ]
        return found

    def references(self, ref: Ref, name: str) -> list[tuple[str, Reference]]:
        rows = self._query(
            ref,
            "SELECT s.path, name, line, kind, scope FROM {snap} s JOIN refs USING (blob_sha) "
            "WHERE version = ? AND name = ? ORDER BY s.path, line, kind",
            (PARSER_VERSION, name),
        )
        return [(row[0], Reference(*row[1:])) for row in rows]

    def symbols_in(self, ref: Ref, path: str) -> list[Symbol]:
        rows = self._query(
            ref,
            f"SELECT {_SYMBOL_COLUMNS} FROM {{snap}} s JOIN symbols USING (blob_sha) "
            "WHERE version = ? AND s.path = ? ORDER BY start_line, qualname",
            (PARSER_VERSION, path),
        )
        return [Symbol(*row) for row in rows]

    def references_in(self, ref: Ref, path: str) -> list[Reference]:
        rows = self._query(
            ref,
            "SELECT name, line, kind, scope FROM {snap} s JOIN refs USING (blob_sha) "
            "WHERE version = ? AND s.path = ? ORDER BY line, name, kind",
            (PARSER_VERSION, path),
        )
        return [Reference(*row) for row in rows]

    def imports(self, ref: Ref) -> list[tuple[str, Import]]:
        rows = self._query(
            ref,
            "SELECT s.path, line, level, module, name, alias FROM {snap} s "
            "JOIN imports USING (blob_sha) WHERE version = ? ORDER BY s.path, line",
            (PARSER_VERSION,),
        )
        return [(row[0], Import(*row[1:])) for row in rows]

    def _query(self, ref: Ref, sql: str, params: Sequence[object]) -> list[Any]:
        self.snapshot(ref)
        return self._connect().execute(sql.format(snap=_table(ref)), params).fetchall()

    def _connect(self) -> sqlite3.Connection:
        if self._db is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)
            self._db = db
        return self._db

    def _index(self, shas: list[str]) -> None:
        db = self._connect()
        missing = self._missing(db, shas)
        if not missing:
            return
        facts = {
            sha: parse_python(data) for sha, data in self.workspace.read_blobs(missing).items()
        }
        db.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have stored some of them while these were parsed.
            for sha in self._missing(db, missing):
                _store(db, sha, facts[sha])
                self.parsed_blobs += 1
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    @staticmethod
    def _missing(db: sqlite3.Connection, shas: list[str]) -> list[str]:
        stored: set[str] = set()
        for i in range(0, len(shas), _LOOKUP_CHUNK):
            chunk = shas[i : i + _LOOKUP_CHUNK]
            marks = ", ".join("?" * len(chunk))
            rows = db.execute(
                f"SELECT blob_sha FROM blobs WHERE version = ? AND blob_sha IN ({marks})",
                (PARSER_VERSION, *chunk),
            )
            stored.update(row[0] for row in rows)
        return [s for s in shas if s not in stored]


def _store(db: sqlite3.Connection, sha: str, facts: FileFacts) -> None:
    v = PARSER_VERSION
    db.execute("INSERT INTO blobs VALUES (?, ?)", (sha, v))
    db.executemany(
        "INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                sha,
                v,
                s.name,
                s.qualname,
                s.kind,
                s.start_line,
                s.def_line,
                s.header_end_line,
                s.end_line,
                s.parent,
            )
            for s in facts.symbols
        ],
    )
    db.executemany(
        "INSERT INTO refs VALUES (?, ?, ?, ?, ?, ?)",
        [(sha, v, r.name, r.line, r.kind, r.scope) for r in facts.references],
    )
    db.executemany(
        "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(sha, v, i.line, i.level, i.module, i.name, i.alias) for i in facts.imports],
    )


def _table(ref: Ref) -> str:
    return "snapshot_head" if ref == "head" else "snapshot_base"
