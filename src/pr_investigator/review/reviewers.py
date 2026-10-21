"""The reviewers, behind one entry point shared by `pri review` and the eval runner.

- b0: one call that sees only the diff (Phase 1 baseline).
- a2: the planner, then one bounded investigation per suspect (D5).
- a1: one bounded agent loop over the whole PR (the ablation for D5).
"""

from enum import StrEnum
from pathlib import Path

from pr_investigator.agent.investigate import a2_prompts, review_a2
from pr_investigator.agent.models import AgentBudget, AgentReview
from pr_investigator.agent.single import a1_prompts, review_a1
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import ReviewTarget
from pr_investigator.errors import ConfigError
from pr_investigator.index.store import RepoIndex
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.prompts import Prompt, load_prompt
from pr_investigator.review.baseline import (
    CONTEXT_PROMPT_VERSION,
    PROMPT_NAME,
    PROMPT_VERSION,
    ReviewResult,
    review_baseline,
)
from pr_investigator.review.context import render_context
from pr_investigator.tools import Toolset
from pr_investigator.trace import Trace
from pr_investigator.workspace import Workspace

AnyReview = ReviewResult | AgentReview


class Reviewer(StrEnum):
    B0 = "b0"
    B1 = "b1"  # B0 plus the deterministic context pack and leads
    A1 = "a1"
    A2 = "a2"

    @property
    def agentic(self) -> bool:
        return self in (Reviewer.A1, Reviewer.A2)


def reviewer_prompts(reviewer: Reviewer, toolset: Toolset = Toolset.BASE) -> tuple[Prompt, ...]:
    if reviewer is Reviewer.A2:
        return a2_prompts(toolset)
    if reviewer is Reviewer.A1:
        return a1_prompts(toolset)
    if reviewer is Reviewer.B1:
        return (load_prompt(PROMPT_NAME, CONTEXT_PROMPT_VERSION),)
    return (load_prompt(PROMPT_NAME, PROMPT_VERSION),)


def check_toolset(reviewer: Reviewer, toolset: Toolset, context: bool = False) -> None:
    if toolset is not Toolset.BASE and not reviewer.agentic:
        raise ConfigError(f"{reviewer.value} uses no tools; --tools applies to a1 and a2 only")
    if context and reviewer not in (Reviewer.A1, Reviewer.B1):
        raise ConfigError("--context applies to a1 (b1 always has it)")


async def run_reviewer(
    reviewer: Reviewer,
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
    index_db: Path | None = None,
    context: bool = False,
) -> AnyReview:
    """Run one review. B0 ignores `budget` and `trace`: it makes a single call.

    The structural toolset needs `index_db`, the SQLite file of the structural index.
    """
    check_toolset(reviewer, toolset, context)
    if reviewer is Reviewer.B0:
        return await review_baseline(
            target, diff, client, max_diff_chars=max_diff_chars, sample=sample
        )
    index = None
    if toolset is Toolset.STRUCTURAL or context or reviewer is Reviewer.B1:
        if index_db is None:
            raise ConfigError("the structural toolset and the context need an index file")
        index = RepoIndex(index_db, workspace)
    try:
        if reviewer is Reviewer.B1:
            assert index is not None
            return await review_baseline(
                target,
                diff,
                client,
                max_diff_chars=max_diff_chars,
                sample=sample,
                context=render_context(workspace, diff, index) or None,
            )
        if reviewer is Reviewer.A2:
            return await review_a2(
                target,
                workspace,
                diff,
                client,
                max_diff_chars=max_diff_chars,
                budget=budget,
                sample=sample,
                trace=trace,
                toolset=toolset,
                index=index,
            )
        return await review_a1(
            target,
            workspace,
            diff,
            client,
            budget=budget,
            sample=sample,
            trace=trace,
            toolset=toolset,
            index=index,
            context=context,
        )
    finally:
        if index is not None:
            index.close()
