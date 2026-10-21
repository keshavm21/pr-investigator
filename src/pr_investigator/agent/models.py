"""What the agentic reviewers (A1 and A2) produce, their budgets, and how findings are built.

Only verified submissions become findings: a confirmed verdict (A2) or a reported finding (A1)
whose evidence passed the mechanical check, anchored on its first defect site. A finding whose
anchor overlaps one already reported is treated as a duplicate and not reported again.
"""

from typing import Literal

from pydantic import BaseModel, Field

from pr_investigator.agent.loop import LoopResult, StopCause
from pr_investigator.agent.planner import DroppedSuspect, Suspect
from pr_investigator.agent.verdict import CheckedVerdict, VerdictStatus
from pr_investigator.domain import (
    Anchor,
    Category,
    Confidence,
    Evidence,
    EvidenceRole,
    Finding,
    ReviewTarget,
    Severity,
)
from pr_investigator.llm.prompts import Prompt
from pr_investigator.llm.types import Message, Usage
from pr_investigator.review.baseline import Coverage
from pr_investigator.tools import Toolset
from pr_investigator.trace import Trace


class AgentBudget(BaseModel, frozen=True):
    """Limits enforced in code (D25 and the accepted Phase 2 choices)."""

    max_suspects: int = Field(default=4, ge=1)
    investigation_turns: int = Field(default=6, ge=1)
    single_agent_turns: int = Field(default=12, ge=1)
    max_tool_calls_per_turn: int = Field(default=3, ge=1)
    max_tool_output_chars: int = Field(default=4_000, ge=1_000)
    # Diff text in opening messages: the suspect's file (A2) and the whole PR (A1).
    investigation_diff_chars: int = Field(default=8_000, ge=1_000)
    single_agent_diff_chars: int = Field(default=24_000, ge=1_000)
    max_findings: int = Field(default=4, ge=1)  # A1 per report; A2 reports at most one per suspect


class LoopRecord(BaseModel):
    """How one agent loop went. The conversation is kept for traces, not serialized here."""

    cause: StopCause
    detail: str | None = None
    turns: int
    cached_turns: int
    tool_calls: int
    retries: int = 0
    observations: int
    usage: Usage
    rejections: list[list[str]] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list, exclude=True)

    @classmethod
    def of[T](cls, result: LoopResult[T]) -> "LoopRecord":
        return cls(
            cause=result.cause,
            detail=result.detail,
            turns=result.turns,
            cached_turns=result.cached_turns,
            tool_calls=result.tool_calls,
            retries=result.retries,
            observations=len(result.ledger),
            usage=result.usage,
            rejections=result.rejections,
            messages=result.messages,
        )


class Investigation(BaseModel):
    """One suspect's investigation (A2)."""

    suspect: Suspect
    status: VerdictStatus
    verdict: CheckedVerdict | None  # the accepted verdict, if any
    loop: LoopRecord
    finding_id: str | None = None  # the finding it produced
    duplicate_of: str | None = None  # confirmed, but the same problem was already reported


class AgentReview(BaseModel):
    reviewer: Literal["a1", "a2"]
    target: ReviewTarget
    summary: str
    findings: list[Finding]
    coverage: Coverage
    provider: str
    model: str
    prompts: dict[str, str] = Field(description="prompt id -> sha256 of its text")
    budget: AgentBudget
    toolset: Toolset = Toolset.BASE
    context: bool = False  # the opening message carried a context pack and leads
    suspects: list[Suspect] = Field(default_factory=list)
    dropped_suspects: list[DroppedSuspect] = Field(default_factory=list)
    investigations: list[Investigation] = Field(default_factory=list)
    single: LoopRecord | None = None  # A1's loop
    usage: Usage = Field(default_factory=Usage)
    requests: int = 0  # model requests, cached ones included
    cached_requests: int = 0
    retries: int = 0  # provider errors retried by the guard, planner included
    duration_seconds: float = 0.0


def emit_run_start(
    trace: Trace,
    reviewer: str,
    target: ReviewTarget,
    provider: str,
    model: str,
    budget: AgentBudget,
    prompts: tuple[Prompt, ...],
    toolset: Toolset = Toolset.BASE,
    context: bool = False,
) -> None:
    trace.emit(
        "run_start",
        reviewer=reviewer,
        target=target,
        provider=provider,
        model=model,
        budget=budget,
        prompts={p.id: p.sha256 for p in prompts},
        toolset=toolset,
        context=context,
    )


def emit_run_end(trace: Trace, review: "AgentReview") -> None:
    trace.emit(
        "run_end",
        summary=review.summary,
        findings=review.findings,
        usage=review.usage,
        requests=review.requests,
        cached_requests=review.cached_requests,
        duration_seconds=review.duration_seconds,
    )


class Findings:
    """Builds the reported findings in order, numbering them and skipping duplicates."""

    def __init__(self) -> None:
        self.items: list[Finding] = []

    def add(
        self,
        *,
        title: str,
        explanation: str,
        category: Category,
        severity: Severity,
        confidence: Confidence,
        suggested_fix: str | None,
        evidence: list[Evidence],
        anchor: Anchor,
    ) -> tuple[str | None, str | None]:
        """Returns (new finding id, None), or (None, id of the finding it duplicates)."""
        # Enforced, not assumed: a reported finding always has a verified defect site.
        if not any(e.role is EvidenceRole.DEFECT_SITE for e in evidence):
            raise ValueError("a finding needs verified defect_site evidence")
        for existing in self.items:
            if overlaps(existing.anchor, anchor):
                return None, existing.id
        finding = Finding(
            id=f"F{len(self.items) + 1}",
            title=title,
            category=category,
            severity=severity,
            confidence=confidence,
            anchor=anchor,
            explanation=explanation,
            suggested_fix=suggested_fix,
            anchor_in_diff=True,  # the evidence check only accepts commentable defect sites
            evidence=evidence,
        )
        self.items.append(finding)
        return finding.id, None


def overlaps(a: Anchor, b: Anchor) -> bool:
    return (
        a.path == b.path
        and a.side is b.side
        and a.start_line <= b.end_line
        and b.start_line <= a.end_line
    )
