"""The skeptic verifier: a fresh agent loop that tries to refute one finding.

It sees the claim, the system-extracted evidence snippets and the diff of the anchored file, and
has a small read-only tool budget. It never sees how the investigator reached the claim. A
refutation must cite evidence that passes the same mechanical check as any other. Anything that
isn't an accepted submission leaves the finding uncertain, never refuted.
"""

from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from pr_investigator.agent.evidence import EvidenceRef, check_evidence, lowercase
from pr_investigator.agent.ledger import ObservationLedger, printable
from pr_investigator.agent.loop import (
    AgentLoop,
    LoopConfig,
    LoopResult,
    Submission,
    validation_problems,
)
from pr_investigator.agent.models import AgentBudget, LoopRecord, SkepticStatus, Verification
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import Evidence, EvidenceRole, Finding, ReviewTarget
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.prompts import Prompt, load_prompt
from pr_investigator.llm.types import Effort, ToolSpec
from pr_investigator.review.baseline import untrusted
from pr_investigator.tools import run_tool
from pr_investigator.trace import Trace
from pr_investigator.workspace import Workspace

STAGE = "verify"
PROMPT_NAME, PROMPT_VERSION = "skeptic", 1
SKEPTIC_TURNS = 5  # model requests per finding


class SkepticVerdict(BaseModel):
    """The arguments of submit_check."""

    _status_case = field_validator("status", mode="before")(lowercase)

    status: SkepticStatus = Field(
        description=(
            "upheld: you looked for reasons the claim is false and found none. "
            "refuted: you found code that shows the claim is wrong (cite it). "
            "uncertain: what you saw isn't enough to decide."
        )
    )
    explanation: str = Field(description="What you checked and found, in a few sentences.")
    evidence: list[EvidenceRef] = Field(
        default_factory=list,
        description="Observations for your outcome. Refuted needs a context or absence item.",
    )


class CheckedSkeptic(BaseModel):
    verdict: SkepticVerdict
    evidence: list[Evidence]


class SubmitCheck:
    name = "submit_check"

    def __init__(self, workspace: Workspace, diff: ParsedDiff) -> None:
        self.workspace = workspace
        self.diff = diff

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=(
                "Finish the check with your outcome. Cite evidence by the observation ids "
                "listed above tool results; only lines you were shown can be cited."
            ),
            parameters=SkepticVerdict.model_json_schema(),
        )

    def check(
        self, arguments: dict[str, Any], ledger: ObservationLedger
    ) -> Submission[CheckedSkeptic]:
        try:
            verdict = SkepticVerdict.model_validate(arguments)
        except ValidationError as exc:
            return Submission(errors=validation_problems(exc))
        result = check_evidence(
            verdict.evidence, ledger, self.workspace, self.diff, require_defect_site=False
        )
        if not result.ok:
            return Submission(errors=result.errors)
        if verdict.status is SkepticStatus.REFUTED and not any(
            e.role in (EvidenceRole.CONTEXT, EvidenceRole.ABSENCE) for e in result.evidence
        ):
            return Submission(
                errors=["a refuted outcome needs at least one context or absence evidence item"]
            )
        return Submission(value=CheckedSkeptic(verdict=verdict, evidence=result.evidence))


def skeptic_prompt() -> Prompt:
    return load_prompt(PROMPT_NAME, PROMPT_VERSION)


def outcome(result: LoopResult[CheckedSkeptic]) -> SkepticStatus:
    """The skeptic's status, or uncertain when it never submitted an accepted check."""
    if result.submission is None:
        return SkepticStatus.UNCERTAIN
    return result.submission.verdict.status


async def verify_finding(
    finding: Finding,
    target: ReviewTarget,
    workspace: Workspace,
    diff: ParsedDiff,
    client: LLMClient,
    *,
    budget: AgentBudget,
    sample: int = 0,
    trace: Trace | None = None,
) -> Verification:
    config = LoopConfig(
        stage=STAGE,
        data_class=target.data_class,
        max_turns=SKEPTIC_TURNS,
        max_tool_calls_per_turn=budget.max_tool_calls_per_turn,
        max_tool_output_chars=budget.max_tool_output_chars,
        effort=Effort.MEDIUM,
        sample=sample,
    )
    loop = AgentLoop(client, workspace, diff, SubmitCheck(workspace, diff), config, trace=trace)
    result = await loop.run(skeptic_prompt().text, skeptic_opening(loop, finding, budget))
    submission = result.submission
    return Verification(
        finding_id=finding.id,
        status=outcome(result),
        explanation=submission.verdict.explanation if submission else "No accepted check.",
        evidence=submission.evidence if submission else [],
        loop=LoopRecord.of(result),
    )


def skeptic_opening(loop: AgentLoop[CheckedSkeptic], finding: Finding, budget: AgentBudget) -> str:
    anchor = finding.anchor
    cited = []
    for item in finding.evidence:
        if item.role is EvidenceRole.ABSENCE:
            cited.append(f"- absence: a search for {item.query} found nothing")
            continue
        cited.append(
            f"- {item.role.value} {printable(item.path or '')}:{item.start_line}-{item.end_line} "
            f"({item.ref}):\n{item.snippet or ''}"
        )
    claim = "\n".join(
        [
            f"Claim: {finding.title}",
            f"Severity: {finding.severity.value}; category: {finding.category.value}",
            f"Explanation: {finding.explanation}",
            f"Location: {anchor.path} lines {anchor.start_line}-{anchor.end_line} "
            f"({'deleted lines' if anchor.side.value == 'LEFT' else 'new version'})",
            "",
            "Lines the reviewer cited:",
            *cited,
        ]
    )
    ctx = loop.ctx
    shown = run_tool("get_diff", {"path": anchor.path}, ctx)
    return "\n".join(
        [
            "Try to refute this claimed problem.",
            "",
            untrusted("claim", claim),
            "",
            f"Budget: {SKEPTIC_TURNS} turns (each reply is one turn), up to "
            f"{budget.max_tool_calls_per_turn} tool calls per turn. Finish with submit_check.",
            "",
            f"The diff of {printable(anchor.path)}:",
            loop.present("diff", shown.content, [] if shown.is_error else shown.observations),
        ]
    )
