"""Reviewer A3 (the full pipeline so far): A2, then an independent skeptic per finding, then
aggregation. The skeptic can only remove a finding with grounded refuting evidence; a finding
it can't decide stays, marked uncertain. See review/aggregate.py for ranking and the budget.
"""

import time

from pr_investigator.agent.investigate import _review_a2, a2_prompts
from pr_investigator.agent.models import (
    AgentBudget,
    AgentReview,
    LoopRecord,
    Verification,
    emit_run_end,
    emit_run_start,
)
from pr_investigator.agent.skeptic import skeptic_prompt, verify_finding
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import ReviewTarget
from pr_investigator.index.store import RepoIndex
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.prompts import Prompt
from pr_investigator.llm.types import Usage
from pr_investigator.review.aggregate import aggregate
from pr_investigator.review.context import scan_leads
from pr_investigator.tools import Toolset
from pr_investigator.trace import Trace, errors_traced
from pr_investigator.workspace import Workspace

REVIEWER = "a3"


def a3_prompts(toolset: Toolset = Toolset.BASE) -> tuple[Prompt, ...]:
    return (*a2_prompts(toolset), skeptic_prompt())


async def review_a3(
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
    prompts = a3_prompts(toolset)
    emit_run_start(trace, REVIEWER, target, client.provider, client.model, budget, prompts, toolset)
    with errors_traced(trace):
        started = time.monotonic()
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
        verifications: list[Verification] = []
        usage: Usage = review.usage
        requests, cached, retries = review.requests, review.cached_requests, review.retries
        for finding in review.findings:  # sequential by design: the rate limit sets the pace
            scoped = trace.scoped(f"verify-{finding.id}")
            scoped.emit("verification_start", finding=finding)
            verification = await verify_finding(
                finding,
                target,
                workspace,
                diff,
                client,
                budget=budget,
                sample=sample,
                trace=scoped,
            )
            scoped.emit(
                "verification_end", status=verification.status, explanation=verification.explanation
            )
            verifications.append(verification)
            record: LoopRecord = verification.loop
            usage = usage + record.usage
            requests += record.turns
            cached += record.cached_turns
            retries += record.retries
        result = aggregate(review.findings, verifications, scan_leads(diff, index))  # verified
        review = review.model_copy(
            update={
                "reviewer": REVIEWER,
                "findings": result.kept,
                "refuted": result.refuted,
                "posted_ids": result.posted_ids,
                "strengths": result.strengths,
                "verifications": verifications,
                "usage": usage,
                "requests": requests,
                "cached_requests": cached,
                "retries": retries,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        )
    emit_run_end(trace, review)
    return review
