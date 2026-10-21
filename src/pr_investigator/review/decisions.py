"""Review decisions: what a human did with each finding, and what was published.

Stored in one SQLite file (`settings.review_db`). The decisions are labelled feedback for
later evaluation: approved, rejected with a reason code, and any edits made first. A
publication row records what was sent, so a retry can't post twice.
"""

import json
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from pr_investigator.domain import Finding, Severity
from pr_investigator.errors import PublishError

MAX_TITLE = 200
MAX_TEXT = 4_000


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ReasonCode(StrEnum):
    FALSE_POSITIVE = "false_positive"
    NOT_IMPORTANT = "not_important"
    DUPLICATE = "duplicate"
    WRONG_LOCATION = "wrong_location"
    UNCLEAR = "unclear"
    OTHER = "other"


class FindingEdit(BaseModel):
    """Changes a reviewer made before approving. A missing field means unchanged."""

    title: str | None = Field(default=None, max_length=MAX_TITLE)
    explanation: str | None = Field(default=None, max_length=MAX_TEXT)
    severity: Severity | None = None
    suggested_fix: str | None = Field(default=None, max_length=MAX_TEXT)

    @property
    def empty(self) -> bool:
        return not self.model_dump(exclude_none=True)


class ReviewDecision(BaseModel):
    run_id: str
    finding_id: str
    decision: Decision
    reviewer: str
    reason_code: ReasonCode | None = None
    reason_text: str = ""
    edit: FindingEdit = Field(default_factory=FindingEdit)
    decided_at: str


class Publication(BaseModel):
    run_id: str
    key: str
    github_review_id: int | None
    html_url: str | None
    commit_id: str
    posted_by: str
    comments: int
    summary_only: int
    published_at: str


def apply_edit(finding: Finding, edit: FindingEdit) -> Finding:
    """The finding as the reviewer edited it. Anchor and evidence can't be edited."""
    return finding.model_copy(update=edit.model_dump(exclude_none=True))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision (
    run_id TEXT NOT NULL, finding_id TEXT NOT NULL, decision TEXT NOT NULL,
    reviewer TEXT NOT NULL, reason_code TEXT, reason_text TEXT NOT NULL, edit TEXT NOT NULL,
    decided_at TEXT NOT NULL, PRIMARY KEY (run_id, finding_id)
);
CREATE TABLE IF NOT EXISTS decision_history (
    run_id TEXT NOT NULL, finding_id TEXT NOT NULL, decision TEXT NOT NULL,
    reviewer TEXT NOT NULL, reason_code TEXT, reason_text TEXT NOT NULL, edit TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication (
    run_id TEXT NOT NULL, key TEXT NOT NULL, github_review_id INTEGER, html_url TEXT,
    commit_id TEXT NOT NULL, posted_by TEXT NOT NULL, comments INTEGER NOT NULL,
    summary_only INTEGER NOT NULL, published_at TEXT NOT NULL, PRIMARY KEY (run_id, key)
);
"""


class DecisionStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def decide(
        self,
        run_id: str,
        finding_id: str,
        decision: Decision,
        *,
        reviewer: str,
        reason_code: ReasonCode | None = None,
        reason_text: str = "",
        edit: FindingEdit | None = None,
    ) -> ReviewDecision:
        if decision is Decision.REJECTED and reason_code is None:
            raise PublishError("a rejection needs a reason code")
        if decision is Decision.APPROVED:
            reason_code, reason_text = None, ""
        edit = edit or FindingEdit()
        made = ReviewDecision(
            run_id=run_id,
            finding_id=finding_id,
            decision=decision,
            reviewer=reviewer,
            reason_code=reason_code,
            reason_text=reason_text[:MAX_TEXT],
            edit=edit,
            decided_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        row = (
            made.run_id,
            made.finding_id,
            made.decision.value,
            made.reviewer,
            made.reason_code.value if made.reason_code else None,
            made.reason_text,
            made.edit.model_dump_json(exclude_none=True),
            made.decided_at,
        )
        self._db.execute("INSERT INTO decision_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
        self._db.execute("INSERT OR REPLACE INTO decision VALUES (?, ?, ?, ?, ?, ?, ?, ?)", row)
        return made

    def clear(self, run_id: str, finding_id: str) -> None:
        self._db.execute(
            "DELETE FROM decision WHERE run_id = ? AND finding_id = ?", (run_id, finding_id)
        )

    def decisions(self, run_id: str) -> dict[str, ReviewDecision]:
        rows = self._db.execute(
            "SELECT run_id, finding_id, decision, reviewer, reason_code, reason_text, edit, "
            "decided_at FROM decision WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return {
            r[1]: ReviewDecision(
                run_id=r[0],
                finding_id=r[1],
                decision=Decision(r[2]),
                reviewer=r[3],
                reason_code=ReasonCode(r[4]) if r[4] else None,
                reason_text=r[5],
                edit=FindingEdit.model_validate(json.loads(r[6])),
                decided_at=r[7],
            )
            for r in rows
        }

    def record_publication(self, publication: Publication) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO publication VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                publication.run_id,
                publication.key,
                publication.github_review_id,
                publication.html_url,
                publication.commit_id,
                publication.posted_by,
                publication.comments,
                publication.summary_only,
                publication.published_at,
            ),
        )

    def publications(self, run_id: str) -> list[Publication]:
        rows = self._db.execute(
            "SELECT run_id, key, github_review_id, html_url, commit_id, posted_by, comments, "
            "summary_only, published_at FROM publication WHERE run_id = ? ORDER BY published_at",
            (run_id,),
        ).fetchall()
        return [
            Publication(**dict(zip(Publication.model_fields, row, strict=True))) for row in rows
        ]
