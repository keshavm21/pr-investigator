"""Core domain types shared by ingestion, review, rendering and evaluation."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(StrEnum):
    SECURITY = "security"
    CORRECTNESS = "correctness"
    RELIABILITY = "reliability"
    PERFORMANCE = "performance"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Side(StrEnum):
    """Which side of the diff a line belongs to, as GitHub's review API names it."""

    RIGHT = "RIGHT"  # head version: added and unchanged lines
    LEFT = "LEFT"  # base version: deleted lines


class DataClass(StrEnum):
    """Who may see a target's content. Decides which LLM providers may receive it."""

    PUBLIC = "public"  # public repository
    TEST = "test"  # our own evaluation fixtures
    PRIVATE = "private"  # private repository, or anything we can't prove is public


class EvidenceRole(StrEnum):
    DEFECT_SITE = "defect_site"  # where the problem is; must be on lines the PR diff shows
    CONTEXT = "context"  # supporting code anywhere: callers, definitions, guards, tests
    ABSENCE = "absence"  # a search that found nothing, e.g. "no validation exists"


class Evidence(BaseModel):
    """One verified evidence item. The snippet is re-read from the repository at the analyzed
    commit by the system; it is never copied from model output."""

    role: EvidenceRole
    observation_id: str
    note: str = Field(description="The model's explanation of why this item matters")
    ref: Literal["head", "base"] | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    snippet: str | None = None
    query: str | None = Field(default=None, description="For absence: the search with no matches")


class Anchor(BaseModel):
    path: str
    side: Side = Side.RIGHT
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class Finding(BaseModel):
    id: str
    title: str
    category: Category
    severity: Severity
    confidence: Confidence
    anchor: Anchor
    explanation: str
    suggested_fix: str | None = None
    anchor_in_diff: bool = Field(
        description="True if every anchored line is a commentable line of the PR diff."
    )
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="Verified evidence with system-extracted snippets (agentic reviewers only)",
    )


class PullRequestRef(BaseModel):
    owner: str
    repo: str
    number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"


class ReviewTarget(BaseModel):
    """One PR (or local branch comparison) at one head commit, ready to review."""

    title: str
    description: str = ""
    base_sha: str
    head_sha: str
    merge_base_sha: str
    data_class: DataClass
    source: str = Field(description="GitHub URL or local path the code came from")
    pull_request: PullRequestRef | None = None
