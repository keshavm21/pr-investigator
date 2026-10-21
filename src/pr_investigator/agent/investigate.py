"""Reviewer A2 (D5): the planner, then one bounded investigation per suspect, one at a time.

Each investigation is a fresh conversation that ends with submit_verdict. Its opening message
holds the suspect, the planner's summary and the diff of the suspect's file, recorded in the
ledger so it can be cited. Only confirmed verdicts with verified evidence become findings.
"""

import time

from pr_investigator.agent.ledger import printable
from pr_investigator.agent.loop import PROMPT_NAME as LOOP_PROMPT_NAME
from pr_investigator.agent.loop import PROMPT_VERSION as LOOP_PROMPT_VERSION
from pr_investigator.agent.loop import AgentLoop, LoopConfig
from pr_investigator.agent.models import (
    AgentBudget,
    AgentReview,
    Findings,
    Investigation,
    LoopRecord,
    emit_run_end,
    emit_run_start,
)
from pr_investigator.agent.planner import Suspect, make_plan, planner_prompt
from pr_investigator.agent.verdict import (
    CheckedVerdict,
    SubmitVerdict,
    VerdictStatus,
    outcome_status,
)
from pr_investigator.diff import ParsedDiff, describe_file
from pr_investigator.domain import ReviewTarget, Side
from pr_investigator.index.store import RepoIndex
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.prompts import Prompt, load_prompt
from pr_investigator.llm.types import Effort
from pr_investigator.review.aggregate import aggregate
from pr_investigator.review.baseline import untrusted
from pr_investigator.review.context import scan_leads
from pr_investigator.tools import ToolContext, ToolResult, Toolset, run_tool
from pr_investigator.trace import Trace, errors_traced
from pr_investigator.workspace import Workspace

STAGE = "investigate"
PROMPT_NAME, PROMPT_VERSION = "investigator", 3
STRUCTURAL_PROMPT_VERSION = 5  # v3 plus the structural tools (v4 was the claim-checking trial)
REVIEWER = "a2"


def investigator_prompt(toolset: Toolset = Toolset.BASE) -> Prompt:
    structural = toolset is Toolset.STRUCTURAL
    return load_prompt(PROMPT_NAME, STRUCTURAL_PROMPT_VERSION if structural else PROMPT_VERSION)


def a2_prompts(toolset: Toolset = Toolset.BASE) -> tuple[Prompt, ...]:
    """Every prompt A2 uses, for run records."""
    return (
        planner_prompt(),
        investigator_prompt(toolset),
        load_prompt(LOOP_PROMPT_NAME, LOOP_PROMPT_VERSION),
    )


async def review_a2(
    target: ReviewTarget,
    workspace: Workspace,
    diff: ParsedDiff,
    client: LLMClient,
    *,
    max_diff_chars: int,
    budget: AgentBudget | None = None,
    sample: int = 0,
    trace: Trace | None = None,
    toolset: Toolset = Toolset.BASE,
    index: RepoIndex | None = None,
) -> AgentReview:
    budget = budget or AgentBudget()
    trace = trace or Trace()
    prompts = a2_prompts(toolset)
    emit_run_start(trace, REVIEWER, target, client.provider, client.model, budget, prompts, toolset)
    with errors_traced(trace):
        review = await _review_a2(
            target,
            workspace,
            diff,
            client,
            max_diff_chars,
            budget,
            sample,
            trace,
            prompts,
            toolset,
            index,
        )
    emit_run_end(trace, review)
    return review


async def _review_a2(
    target: ReviewTarget,
    workspace: Workspace,
    diff: ParsedDiff,
    client: LLMClient,
    max_diff_chars: int,
    budget: AgentBudget,
    sample: int,
    trace: Trace,
    prompts: tuple[Prompt, ...],
    toolset: Toolset,
    index: RepoIndex | None,
) -> AgentReview:
    started = time.monotonic()
    plan = await make_plan(
        target,
        diff,
        client,
        max_diff_chars=max_diff_chars,
        max_suspects=budget.max_suspects,
        sample=sample,
    )
    trace.emit(
        "plan",
        summary=plan.summary,
        suspects=plan.suspects,
        dropped=plan.dropped,
        coverage=plan.coverage,
        usage=plan.usage,
        attempts=plan.attempts,
        cached=plan.cached,
        retries=plan.retries,
    )
    usage = plan.usage
    requests = plan.attempts
    cached_requests = plan.attempts if plan.cached else 0
    retries = plan.retries
    findings = Findings()
    investigations: list[Investigation] = []
    for suspect in plan.suspects:  # sequential by design: the rate limit sets the pace
        scoped = trace.scoped(suspect.id)
        scoped.emit("investigation_start", suspect=suspect)
        investigation = await investigate(
            suspect,
            plan.summary,
            target,
            workspace,
            diff,
            client,
            budget=budget,
            sample=sample,
            trace=scoped,
            toolset=toolset,
            index=index,
        )
        verdict = investigation.verdict
        if investigation.status is VerdictStatus.CONFIRMED and verdict is not None:
            finding_id, duplicate_of = _report(findings, verdict)
            investigation = investigation.model_copy(
                update={"finding_id": finding_id, "duplicate_of": duplicate_of}
            )
        scoped.emit(
            "investigation_end",
            status=investigation.status,
            claim=verdict.verdict.claim if verdict is not None else None,
            finding_id=investigation.finding_id,
            duplicate_of=investigation.duplicate_of,
        )
        investigations.append(investigation)
        usage = usage + investigation.loop.usage
        requests += investigation.loop.turns
        cached_requests += investigation.loop.cached_turns
        retries += investigation.loop.retries

    ranked = aggregate(findings.items, [], scan_leads(diff, index), verified=False)
    return AgentReview(
        reviewer=REVIEWER,
        target=target,
        summary=plan.summary,
        findings=ranked.kept,
        posted_ids=ranked.posted_ids,
        strengths=ranked.strengths,
        coverage=plan.coverage,
        provider=client.provider,
        model=client.model,
        prompts={p.id: p.sha256 for p in prompts},
        budget=budget,
        toolset=toolset,
        suspects=plan.suspects,
        dropped_suspects=plan.dropped,
        investigations=investigations,
        usage=usage,
        requests=requests,
        cached_requests=cached_requests,
        retries=retries,
        duration_seconds=round(time.monotonic() - started, 3),
    )


async def investigate(
    suspect: Suspect,
    summary: str,
    target: ReviewTarget,
    workspace: Workspace,
    diff: ParsedDiff,
    client: LLMClient,
    *,
    budget: AgentBudget,
    sample: int = 0,
    trace: Trace | None = None,
    toolset: Toolset = Toolset.BASE,
    index: RepoIndex | None = None,
) -> Investigation:
    config = LoopConfig(
        stage=STAGE,
        data_class=target.data_class,
        max_turns=budget.investigation_turns,
        max_tool_calls_per_turn=budget.max_tool_calls_per_turn,
        max_tool_output_chars=budget.max_tool_output_chars,
        effort=Effort.MEDIUM,
        sample=sample,
    )
    loop = AgentLoop(
        client,
        workspace,
        diff,
        SubmitVerdict(workspace, diff),
        config,
        tools=toolset.tools,
        index=index,
        trace=trace,
    )
    opening = investigation_opening(loop, suspect, summary, budget)
    result = await loop.run(investigator_prompt(toolset).text, opening)
    return Investigation(
        suspect=suspect,
        status=outcome_status(result),
        verdict=result.submission,
        loop=LoopRecord.of(result),
    )


def investigation_opening(
    loop: AgentLoop[CheckedVerdict], suspect: Suspect, summary: str, budget: AgentBudget
) -> str:
    where = "deleted lines" if suspect.side is Side.LEFT else "new version"
    changed = "\n".join(describe_file(f) for f in loop.ctx.diff.files)
    lines = [
        "Investigate this suspected problem.",
        "",
        f"Suspect {suspect.id}: {suspect.title}",
        f"Category: {suspect.category.value}",
        f"Location: {printable(suspect.path)} lines {suspect.start_line}-{suspect.end_line} "
        f"({where})",
        f"Suspected problem: {suspect.problem}",
        f"What would confirm or refute it: {suspect.how_to_check}",
        "",
        f"The planner's summary of the pull request: {summary}",
        "",
        f"Budget: {budget.investigation_turns} turns (each reply is one turn), up to "
        f"{budget.max_tool_calls_per_turn} tool calls per turn. Finish with submit_verdict.",
        "",
        "Files the pull request changes:",
        untrusted("files", changed),
        "",
        f"The diff of {printable(suspect.path)}:",
        _suspect_diff(loop, suspect, budget.investigation_diff_chars),
    ]
    return "\n".join(lines)


def _suspect_diff(loop: AgentLoop[CheckedVerdict], suspect: Suspect, max_chars: int) -> str:
    """The suspect's file diff, starting at the suspect's hunk if the whole file doesn't fit."""
    ctx = ToolContext(workspace=loop.ctx.workspace, diff=loop.ctx.diff, max_output_chars=max_chars)
    result = run_tool("get_diff", {"path": suspect.path}, ctx)
    file = loop.ctx.diff.file(suspect.path)
    if file is not None and result.truncated and not _shows(result, suspect):
        hunk = next(
            (
                n
                for n, h in enumerate(file.hunks, 1)
                if h.contains(suspect.side, suspect.start_line)
            ),
            None,
        )
        if hunk is not None:
            result = run_tool("get_diff", {"path": suspect.path, "start_hunk": hunk}, ctx)
    return loop.present("diff", result.content, [] if result.is_error else result.observations)


def _shows(result: ToolResult, suspect: Suspect) -> bool:
    ref = "base" if suspect.side is Side.LEFT else "head"
    return any(
        o.ref == ref
        and o.start_line is not None
        and o.end_line is not None
        and o.start_line <= suspect.start_line <= o.end_line
        for o in result.observations
    )


def _report(findings: Findings, checked: CheckedVerdict) -> tuple[str | None, str | None]:
    verdict = checked.verdict
    assert checked.anchor is not None  # a confirmed verdict passed the defect-site check
    return findings.add(
        title=verdict.claim,
        explanation=verdict.explanation,
        category=verdict.category,
        severity=verdict.severity,
        confidence=verdict.confidence,
        suggested_fix=verdict.suggested_fix,
        evidence=checked.evidence,
        anchor=checked.anchor,
    )
