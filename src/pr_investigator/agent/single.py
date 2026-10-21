"""Reviewer A1 (ablation for D5): one bounded agent loop over the whole PR, no planner.

It uses the same loop, tools and evidence check as A2, with a larger turn budget, and ends with
report_findings. The opening message holds the PR's metadata and its diff, rendered and limited
the way B0 renders it, with the diff lines recorded in the ledger so they can be cited.
"""

import time
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from pr_investigator.agent.evidence import EvidenceRef, check_evidence, lowercase
from pr_investigator.agent.ledger import ObservationLedger, printable
from pr_investigator.agent.loop import PROMPT_NAME as LOOP_PROMPT_NAME
from pr_investigator.agent.loop import PROMPT_VERSION as LOOP_PROMPT_VERSION
from pr_investigator.agent.loop import AgentLoop, LoopConfig, Submission, validation_problems
from pr_investigator.agent.models import (
    AgentBudget,
    AgentReview,
    Findings,
    LoopRecord,
    emit_run_end,
    emit_run_start,
)
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import Anchor, Category, Confidence, Evidence, ReviewTarget, Severity
from pr_investigator.index.store import RepoIndex
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.prompts import Prompt, load_prompt
from pr_investigator.llm.types import Effort, ToolSpec
from pr_investigator.review.baseline import Coverage, render_diff, untrusted
from pr_investigator.review.context import build_context_pack, find_leads
from pr_investigator.tools import Observation, Toolset
from pr_investigator.tools.get_diff import file_observations
from pr_investigator.trace import Trace, errors_traced
from pr_investigator.workspace import Workspace

STAGE = "single_agent"
PROMPT_NAME, PROMPT_VERSION = "single_agent", 3
STRUCTURAL_PROMPT_VERSION = 5  # v3 plus the structural tools (v4 was the claim-checking trial)
REVIEWER = "a1"


class ReportedFinding(BaseModel):
    _enum_case = field_validator("category", "severity", "confidence", mode="before")(lowercase)

    claim: str = Field(description="One sentence: the problem.")
    explanation: str = Field(description="What goes wrong, when, and the impact.")
    category: Category
    severity: Severity
    confidence: Confidence
    evidence: list[EvidenceRef] = Field(description="At least one defect_site item.")
    suggested_fix: str | None = Field(default=None, description="Optional: a short fix.")


class Report(BaseModel):
    """The arguments of report_findings."""

    summary: str = Field(description="One or two sentences on what the pull request changes")
    findings: list[ReportedFinding] = Field(
        description="The problems you found; an empty list if there are none."
    )


class CheckedFinding(BaseModel):
    finding: ReportedFinding
    evidence: list[Evidence]
    anchor: Anchor


class CheckedReport(BaseModel):
    summary: str
    findings: list[CheckedFinding]


class ReportFindings:
    name = "report_findings"

    def __init__(self, workspace: Workspace, diff: ParsedDiff, *, max_findings: int) -> None:
        self.workspace = workspace
        self.diff = diff
        self.max_findings = max_findings

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=(
                f"Finish the review with the problems you found, at most {self.max_findings}, "
                "or an empty list. Cite evidence by the observation ids listed above tool "
                "results; only lines you were shown can be cited."
            ),
            parameters=Report.model_json_schema(),
        )

    def check(
        self, arguments: dict[str, Any], ledger: ObservationLedger
    ) -> Submission[CheckedReport]:
        try:
            report = Report.model_validate(arguments)
        except ValidationError as exc:
            return Submission(errors=validation_problems(exc))
        if len(report.findings) > self.max_findings:
            return Submission(
                errors=[
                    f"you reported {len(report.findings)} findings; the limit is "
                    f"{self.max_findings}. Keep the best-supported ones."
                ]
            )
        errors: list[str] = []
        checked: list[CheckedFinding] = []
        for number, finding in enumerate(report.findings, 1):
            result = check_evidence(
                finding.evidence, ledger, self.workspace, self.diff, require_defect_site=True
            )
            if not result.ok or result.anchor is None:
                errors += [f"finding {number}: {error}" for error in result.errors]
                continue
            checked.append(
                CheckedFinding(finding=finding, evidence=result.evidence, anchor=result.anchor)
            )
        if errors:
            return Submission(errors=errors)
        return Submission(value=CheckedReport(summary=report.summary, findings=checked))


def single_agent_prompt(toolset: Toolset = Toolset.BASE) -> Prompt:
    structural = toolset is Toolset.STRUCTURAL
    return load_prompt(PROMPT_NAME, STRUCTURAL_PROMPT_VERSION if structural else PROMPT_VERSION)


def a1_prompts(toolset: Toolset = Toolset.BASE) -> tuple[Prompt, ...]:
    """Every prompt A1 uses, for run records."""
    return (single_agent_prompt(toolset), load_prompt(LOOP_PROMPT_NAME, LOOP_PROMPT_VERSION))


async def review_a1(
    target: ReviewTarget,
    workspace: Workspace,
    diff: ParsedDiff,
    client: LLMClient,
    *,
    budget: AgentBudget | None = None,
    sample: int = 0,
    trace: Trace | None = None,
    toolset: Toolset = Toolset.BASE,
    index: RepoIndex | None = None,
    context: bool = False,
) -> AgentReview:
    budget = budget or AgentBudget()
    trace = trace or Trace()
    prompts = a1_prompts(toolset)
    emit_run_start(
        trace, REVIEWER, target, client.provider, client.model, budget, prompts, toolset, context
    )
    with errors_traced(trace):
        review = await _review_a1(
            target, workspace, diff, client, budget, sample, trace, prompts, toolset, index, context
        )
    emit_run_end(trace, review)
    return review


async def _review_a1(
    target: ReviewTarget,
    workspace: Workspace,
    diff: ParsedDiff,
    client: LLMClient,
    budget: AgentBudget,
    sample: int,
    trace: Trace,
    prompts: tuple[Prompt, ...],
    toolset: Toolset,
    index: RepoIndex | None,
    context: bool,
) -> AgentReview:
    started = time.monotonic()
    config = LoopConfig(
        stage=STAGE,
        data_class=target.data_class,
        max_turns=budget.single_agent_turns,
        max_tool_calls_per_turn=budget.max_tool_calls_per_turn,
        max_tool_output_chars=budget.max_tool_output_chars,
        effort=Effort.MEDIUM,
        sample=sample,
    )
    terminal = ReportFindings(workspace, diff, max_findings=budget.max_findings)
    loop = AgentLoop(
        client,
        workspace,
        diff,
        terminal,
        config,
        tools=toolset.tools,
        index=index,
        trace=trace.scoped("review"),
    )
    opening, coverage = single_agent_opening(loop, target, budget, with_context=context)
    review = AgentReview(
        reviewer=REVIEWER,
        target=target,
        summary="No reviewable changes.",
        findings=[],
        coverage=coverage,
        provider=client.provider,
        model=client.model,
        prompts={p.id: p.sha256 for p in prompts},
        budget=budget,
        toolset=toolset,
        context=context,
    )
    if not coverage.reviewed:
        return review  # nothing to show, so spend no quota

    result = await loop.run(prompts[0].text, opening)
    record = LoopRecord.of(result)
    findings = Findings()
    summary = f"The review ended without an accepted report ({result.cause.value})."
    if result.submission is not None:
        summary = result.submission.summary
        for checked in result.submission.findings:
            out = checked.finding
            findings.add(
                title=out.claim,
                explanation=out.explanation,
                category=out.category,
                severity=out.severity,
                confidence=out.confidence,
                suggested_fix=out.suggested_fix,
                evidence=checked.evidence,
                anchor=checked.anchor,
            )
    return review.model_copy(
        update={
            "summary": summary,
            "findings": findings.items,
            "single": record,
            "usage": record.usage,
            "requests": record.turns,
            "cached_requests": record.cached_turns,
            "retries": record.retries,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    )


def single_agent_opening(
    loop: AgentLoop[CheckedReport],
    target: ReviewTarget,
    budget: AgentBudget,
    *,
    with_context: bool = False,
) -> tuple[str, Coverage]:
    diff = loop.ctx.diff
    diff_text, coverage = render_diff(diff, budget.single_agent_diff_chars)
    observations: list[Observation] = []
    for path in coverage.reviewed:
        file = diff.file(path)
        assert file is not None
        observations += file_observations(file)
    metadata = f"Title: {target.title}\n\nDescription:\n{target.description or '(none)'}"
    lines = [
        "Review this pull request.",
        "",
        untrusted("pr", metadata),
        "",
        f"Changed files: {len(diff.files)}. Shown below: {len(coverage.reviewed)}.",
    ]
    if coverage.skipped:
        lines.append("Not shown (use get_diff with a path to see one):")
        lines += [f"- {printable(path)} ({reason})" for path, reason in coverage.skipped.items()]
    lines += [
        "",
        f"Budget: {budget.single_agent_turns} turns (each reply is one turn), up to "
        f"{budget.max_tool_calls_per_turn} tool calls per turn. Finish with report_findings.",
        "",
        loop.present("diff", diff_text, observations),
    ]
    if with_context and loop.ctx.index is not None:
        lines += _context_lines(loop)
    return "\n".join(lines), coverage


def _context_lines(loop: AgentLoop[CheckedReport]) -> list[str]:
    index = loop.ctx.index
    assert index is not None
    pack = build_context_pack(loop.ctx.workspace, loop.ctx.diff, index)
    leads = find_leads(loop.ctx.diff, index)
    lines: list[str] = []
    if pack.text:
        lines += [
            "",
            "Context computed from the repository index for the Python functions this PR "
            "changes (callers, callees and tests are matched by name):",
            loop.present("context", pack.text, pack.observations),
        ]
    if leads:
        lines += [
            "",
            "Mechanical leads from scanning the diff. They are suspicions, not findings: check "
            "each with your tools, and mention in your summary any you dismissed.",
            untrusted("leads", "\n".join(f"- {printable(lead)}" for lead in leads)),
        ]
    return lines
