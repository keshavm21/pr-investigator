"""The planner (D5): one structured call that turns a PR into a few suspects to investigate.

It sees what B0 sees: the PR's untrusted-tagged metadata and line-numbered diff, nothing else.
Code then keeps the suspects whose location is in a changed file the planner was shown, orders
them by priority and keeps the first `max_suspects`. Everything dropped is kept with a reason.
"""

from pydantic import BaseModel, Field

from pr_investigator.agent.ledger import printable
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import Category, ReviewTarget, Side
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.prompts import Prompt, load_prompt
from pr_investigator.llm.types import Effort, LLMRequest, Message, Usage
from pr_investigator.review.baseline import Coverage, normalize_path, render_diff, untrusted

STAGE = "plan"
PROMPT_NAME, PROMPT_VERSION = "planner", 1


class SuspectOut(BaseModel):
    """One suspect as the planner writes it."""

    title: str = Field(description="One-line summary of the suspected problem")
    category: Category
    path: str = Field(description="File path exactly as shown in the diff headers")
    side: Side = Field(description="RIGHT for added/unchanged lines, LEFT for deleted lines")
    start_line: int
    end_line: int
    problem: str = Field(description="What might go wrong, and why it is plausible")
    how_to_check: str = Field(
        description="What in the repository would confirm or refute it (callers, guards, ...)"
    )
    priority: int = Field(ge=1, le=10, description="1 for the most likely serious problem")


class PlannerOutput(BaseModel):
    summary: str = Field(description="One or two sentences on what the pull request changes")
    suspects: list[SuspectOut]


class Suspect(SuspectOut):
    id: str  # S1, S2, ... in investigation order


class DroppedSuspect(BaseModel):
    suspect: SuspectOut
    reason: str


class Plan(BaseModel):
    summary: str
    suspects: list[Suspect]
    dropped: list[DroppedSuspect] = Field(default_factory=list)
    coverage: Coverage
    usage: Usage = Field(default_factory=Usage)
    attempts: int = 0  # model requests, a repair included
    cached: bool = False


def planner_prompt() -> Prompt:
    return load_prompt(PROMPT_NAME, PROMPT_VERSION)


def build_plan_request(
    target: ReviewTarget, diff: ParsedDiff, *, max_diff_chars: int, sample: int = 0
) -> tuple[LLMRequest, Coverage]:
    diff_text, coverage = render_diff(diff, max_diff_chars)
    metadata = f"Title: {target.title}\n\nDescription:\n{target.description or '(none)'}"
    lines = [
        "Plan the review of this pull request.",
        "",
        untrusted("pr", metadata),
        "",
        f"Changed files: {len(diff.files)}. Shown below: {len(coverage.reviewed)}.",
    ]
    if coverage.skipped:
        lines.append("Not shown:")
        lines += [f"- {printable(path)} ({reason})" for path, reason in coverage.skipped.items()]
    lines += ["", untrusted("diff", diff_text)]
    request = LLMRequest(
        stage=STAGE,
        data_class=target.data_class,
        system=planner_prompt().text,
        messages=[Message.user("\n".join(lines))],
        output_schema=PlannerOutput,
        effort=Effort.MEDIUM,
        sample=sample,
    )
    return request, coverage


async def make_plan(
    target: ReviewTarget,
    diff: ParsedDiff,
    client: LLMClient,
    *,
    max_diff_chars: int,
    max_suspects: int,
    sample: int = 0,
) -> Plan:
    request, coverage = build_plan_request(
        target, diff, max_diff_chars=max_diff_chars, sample=sample
    )
    if not coverage.reviewed:
        # Nothing to show the planner, so spend no quota.
        return Plan(summary="No reviewable changes.", suspects=[], coverage=coverage)
    response = await client.generate(request)
    assert isinstance(response.parsed, PlannerOutput)
    suspects, dropped = select_suspects(
        response.parsed.suspects, coverage, diff, max_suspects=max_suspects
    )
    return Plan(
        summary=response.parsed.summary,
        suspects=suspects,
        dropped=dropped,
        coverage=coverage,
        usage=response.usage,
        attempts=response.attempts,
        cached=response.cached,
    )


def select_suspects(
    proposed: list[SuspectOut], coverage: Coverage, diff: ParsedDiff, *, max_suspects: int
) -> tuple[list[Suspect], list[DroppedSuspect]]:
    """Keep suspects located in a file the planner was shown; then the top ones by priority."""
    dropped: list[DroppedSuspect] = []
    located: list[SuspectOut] = []
    for out in proposed:
        path = normalize_path(out.path, diff)
        file = diff.file(path)
        if file is None or file.path not in coverage.reviewed:
            dropped.append(
                DroppedSuspect(suspect=out, reason=f"{out.path} isn't a shown changed file")
            )
            continue
        start, end = sorted((max(out.start_line, 1), max(out.end_line, 1)))
        located.append(
            out.model_copy(update={"path": file.path, "start_line": start, "end_line": end})
        )
    ranked = sorted(located, key=lambda s: s.priority)  # stable: ties keep the planner's order
    for out in ranked[max_suspects:]:
        dropped.append(
            DroppedSuspect(suspect=out, reason=f"over the limit of {max_suspects} suspects")
        )
    suspects = [
        Suspect(id=f"S{index}", **out.model_dump())
        for index, out in enumerate(ranked[:max_suspects], 1)
    ]
    return suspects, dropped
