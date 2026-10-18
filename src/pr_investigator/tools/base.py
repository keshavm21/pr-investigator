"""The read-only tool interface.

Every tool takes validated arguments (a Pydantic model), returns text for the model plus
structured observations, and never modifies anything.

Observations record exactly what the model was shown: a tool that cuts its output to fit the
character budget records only the lines it actually displayed. The evidence check relies on
this, because evidence may only cite lines inside an observation.
"""

import hashlib
from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, Field

from pr_investigator.diff import ParsedDiff
from pr_investigator.workspace import Workspace

Ref = Literal["head", "base"]

MAX_OUTPUT_CHARS = 32_000
# Room kept free in every budget for a closing hint ("continue with ...").
HINT_RESERVE = 400


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
    max_output_chars: int = MAX_OUTPUT_CHARS  # the agent uses a much smaller budget


class OutputBudget:
    """Collects output lines up to a character limit, keeping room for a closing hint."""

    def __init__(self, max_chars: int) -> None:
        self.limit = max(max_chars - HINT_RESERVE, 200)
        self.lines: list[str] = []
        self.used = 0

    def fits(self, *lines: str) -> bool:
        return self.used + sum(len(line) + 1 for line in lines) <= self.limit

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)
        self.used += sum(len(line) + 1 for line in lines)

    def text(self) -> str:
        return "\n".join(self.lines)


class Tool(Protocol):
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]

    def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="surrogateescape")).hexdigest()


def lines_sha256(lines: list[str]) -> str:
    """The content hash every observation uses: the file's lines joined with newlines."""
    return sha256_text("\n".join(lines))


def clip_line(text: str, limit: int = 500) -> str:
    """Shorten one very long line for display. Hashes always use the full line."""
    return text if len(text) <= limit else text[:limit] + " [line truncated]"
