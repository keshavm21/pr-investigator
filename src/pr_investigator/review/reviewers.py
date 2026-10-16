"""The reviewers, behind one entry point shared by `pri review` and the eval runner.

- b0: one call that sees only the diff (Phase 1 baseline).
- a2: the planner, then one bounded investigation per suspect (D5).
- a1: one bounded agent loop over the whole PR (the ablation for D5).
"""

from enum import StrEnum

from pr_investigator.agent.investigate import a2_prompts, review_a2
from pr_investigator.agent.models import AgentBudget, AgentReview
from pr_investigator.agent.single import a1_prompts, review_a1
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import ReviewTarget
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.prompts import Prompt, load_prompt
from pr_investigator.review.baseline import (
    PROMPT_NAME,
    PROMPT_VERSION,
    ReviewResult,
    review_baseline,
)
from pr_investigator.trace import Trace
from pr_investigator.workspace import Workspace

AnyReview = ReviewResult | AgentReview


class Reviewer(StrEnum):
    B0 = "b0"
    A1 = "a1"
    A2 = "a2"

    @property
    def agentic(self) -> bool:
        return self is not Reviewer.B0


def reviewer_prompts(reviewer: Reviewer) -> tuple[Prompt, ...]:
    if reviewer is Reviewer.A2:
        return a2_prompts()
    if reviewer is Reviewer.A1:
        return a1_prompts()
    return (load_prompt(PROMPT_NAME, PROMPT_VERSION),)


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
) -> AnyReview:
    """Run one review. B0 ignores `budget` and `trace`: it makes a single call."""
    if reviewer is Reviewer.B0:
        return await review_baseline(
            target, diff, client, max_diff_chars=max_diff_chars, sample=sample
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
        )
    return await review_a1(
        target, workspace, diff, client, budget=budget, sample=sample, trace=trace
    )
