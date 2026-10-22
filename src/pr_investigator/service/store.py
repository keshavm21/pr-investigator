"""The service database: webhook deliveries, installations and the job queue.

One SQLite file (`settings.service_db`), so the whole service is one VM and one database. Every
state change is a single UPDATE guarded by the expected old state, so two workers can't take the
same job, and a job superseded by a newer push is never resurrected.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

QUEUED, FETCHING, READY, ANALYZING = "queued", "fetching", "ready", "analyzing"
AWAITING_REVIEW, FAILED, SUPERSEDED = "awaiting_review", "failed", "superseded"
SKIPPED = "skipped"  # the repository config disabled the service for this repository
ACTIVE = (QUEUED, FETCHING, READY, ANALYZING)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_delivery (
    delivery_id TEXT PRIMARY KEY, event TEXT NOT NULL, action TEXT, received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS installation (
    installation_id INTEGER PRIMARY KEY, account TEXT NOT NULL, state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job (
    id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL, installation_id INTEGER NOT NULL, delivery_id TEXT NOT NULL,
    state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    run_id TEXT, error TEXT, payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS job_by_pr ON job (repo, pr_number, state);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Job:
    id: int
    repo: str  # "owner/name"
    pr_number: int
    head_sha: str
    installation_id: int
    delivery_id: str
    state: str
    run_id: str | None
    error: str | None
    payload: dict[str, Any]


class ServiceStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # Deliveries ---------------------------------------------------------------------------

    def record_delivery(self, delivery_id: str, event: str, action: str | None) -> bool:
        """True the first time a delivery id is seen; False for a redelivery."""
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO webhook_delivery VALUES (?, ?, ?, ?)",
            (delivery_id, event, action, now()),
        )
        return cursor.rowcount == 1

    # Installations ------------------------------------------------------------------------

    def set_installation(self, installation_id: int, account: str, state: str) -> None:
        self._db.execute(
            "INSERT INTO installation VALUES (?, ?, ?, ?) ON CONFLICT(installation_id) DO "
            "UPDATE SET account = excluded.account, state = excluded.state, "
            "updated_at = excluded.updated_at",
            (installation_id, account, state, now()),
        )

    def installation_state(self, installation_id: int) -> str | None:
        row = self._db.execute(
            "SELECT state FROM installation WHERE installation_id = ?", (installation_id,)
        ).fetchone()
        return str(row[0]) if row else None

    # Jobs ---------------------------------------------------------------------------------

    def enqueue(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        installation_id: int,
        delivery_id: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[Job, list[int]]:
        """Add a job for a new head, and supersede the PR's older active jobs (a new push makes
        an analysis of the old head pointless). Returns the job and the superseded ids."""
        stamp = now()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            old = [
                int(r[0])
                for r in self._db.execute(
                    f"SELECT id FROM job WHERE repo = ? AND pr_number = ? "
                    f"AND state IN ({','.join('?' * len(ACTIVE))})",
                    (repo, pr_number, *ACTIVE),
                )
            ]
            for job_id in old:
                self._db.execute(
                    "UPDATE job SET state = ?, updated_at = ? WHERE id = ?",
                    (SUPERSEDED, stamp, job_id),
                )
            cursor = self._db.execute(
                "INSERT INTO job (repo, pr_number, head_sha, installation_id, delivery_id, state, "
                "created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    repo,
                    pr_number,
                    head_sha,
                    installation_id,
                    delivery_id,
                    QUEUED,
                    stamp,
                    stamp,
                    json.dumps(payload or {}),
                ),
            )
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        job = self.get(int(cursor.lastrowid or 0))
        assert job is not None
        return job, old

    def supersede_pull_request(self, repo: str, pr_number: int) -> list[int]:
        """Stop every active job of a pull request (it was closed). Returns their ids."""
        ids = [
            int(r[0])
            for r in self._db.execute(
                f"SELECT id FROM job WHERE repo = ? AND pr_number = ? "
                f"AND state IN ({','.join('?' * len(ACTIVE))})",
                (repo, pr_number, *ACTIVE),
            )
        ]
        for job_id in ids:
            self._db.execute(
                "UPDATE job SET state = ?, updated_at = ? WHERE id = ?",
                (SUPERSEDED, now(), job_id),
            )
        return ids

    def claim(self, from_state: str, to_state: str) -> Job | None:
        """Take the oldest job in `from_state`, atomically."""
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT id FROM job WHERE state = ? ORDER BY id LIMIT 1", (from_state,)
            ).fetchone()
            if row is None:
                self._db.execute("COMMIT")
                return None
            self._db.execute(
                "UPDATE job SET state = ?, updated_at = ? WHERE id = ? AND state = ?",
                (to_state, now(), row[0], from_state),
            )
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        return self.get(int(row[0]))

    def advance(
        self,
        job_id: int,
        from_state: str,
        to_state: str,
        *,
        run_id: str | None = None,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """Move a job on, unless something else (a newer push) already changed its state."""
        cursor = self._db.execute(
            "UPDATE job SET state = ?, updated_at = ?, run_id = COALESCE(?, run_id), "
            "payload = COALESCE(?, payload), error = COALESCE(?, error) "
            "WHERE id = ? AND state = ?",
            (
                to_state,
                now(),
                run_id,
                json.dumps(payload) if payload is not None else None,
                error,
                job_id,
                from_state,
            ),
        )
        return cursor.rowcount == 1

    def fail(self, job_id: int, error: str) -> None:
        self._db.execute(
            "UPDATE job SET state = ?, error = ?, updated_at = ? WHERE id = ? AND state IN "
            f"({','.join('?' * len(ACTIVE))})",
            (FAILED, error[:500], now(), job_id, *ACTIVE),
        )

    def get(self, job_id: int) -> Job | None:
        row = self._db.execute(
            "SELECT id, repo, pr_number, head_sha, installation_id, delivery_id, state, run_id, "
            "error, payload FROM job WHERE id = ?",
            (job_id,),
        ).fetchone()
        return _job(row) if row else None

    def for_run(self, run_id: str) -> Job | None:
        row = self._db.execute(
            "SELECT id, repo, pr_number, head_sha, installation_id, delivery_id, state, run_id, "
            "error, payload FROM job WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return _job(row) if row else None

    def jobs_today(self, installation_id: int) -> int:
        """Jobs created for an installation since midnight UTC, superseded ones included."""
        midnight = datetime.now(UTC).strftime("%Y-%m-%dT00:00:00")
        row = self._db.execute(
            "SELECT COUNT(*) FROM job WHERE installation_id = ? AND created_at >= ?",
            (installation_id, midnight),
        ).fetchone()
        return int(row[0])


def _job(row: tuple[Any, ...]) -> Job:
    return Job(
        id=row[0],
        repo=row[1],
        pr_number=row[2],
        head_sha=row[3],
        installation_id=row[4],
        delivery_id=row[5],
        state=row[6],
        run_id=row[7],
        error=row[8],
        payload=json.loads(row[9]),
    )
