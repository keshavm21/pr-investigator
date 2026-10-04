"""The read-only tool interface.

Every tool takes validated arguments (a Pydantic model), returns text for the model plus
structured observations, and never modifies anything. Observations record exactly what the
model was shown; the Phase 2 evidence ledger checks findings against them.
"""

import hashlib
from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, Field

from pr_investigator.diff import ParsedDiff
from pr_investigator.workspace import Workspace

Ref = Literal["head", "base"]

MAX_OUTPUT_CHARS = 32_000


class ToolError(Exception):
    """A tool couldn't run. The message is shown to the model, so make it actionable."""


class Observation(BaseModel):
    """A record of repository content shown to the model."""

    tool: str
    kind: Literal["lines", "search"]
    ref: Ref = "head"
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    query: str | None = None
    match_count: int | None = None
    content_sha256: str | None = None


class ToolResult(BaseModel):
    content: str
    is_error: bool = False
    truncated: bool = False
    observations: list[Observation] = Field(default_factory=list)


@dataclass
class ToolContext:
    workspace: Workspace
    diff: ParsedDiff


class Tool(Protocol):
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="surrogateescape")).hexdigest()


def clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    """Bound tool output so one call can't flood the model's context."""
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n[output truncated]", True
