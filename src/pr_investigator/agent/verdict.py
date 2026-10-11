"""submit_verdict: the terminal tool of one investigation, and what the investigation concluded.

A verdict is accepted only if its arguments are valid and every evidence item passes the
mechanical evidence check. A confirmed verdict also needs a defect site on lines the PR diff
shows, which becomes its anchor. Anything that isn't an accepted verdict ends inconclusive.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from pr_investigator.agent.evidence import EvidenceRef, check_evidence
from pr_investigator.agent.ledger import ObservationLedger
from pr_investigator.agent.loop import LoopResult, Submission
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import Anchor, Category, Confidence, Evidence, Severity
from pr_investigator.llm.types import ToolSpec
from pr_investigator.workspace import Workspace

MAX_VALIDATION_PROBLEMS = 8


class VerdictStatus(StrEnum):
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


class Verdict(BaseModel):
    """The arguments of submit_verdict."""

    status: VerdictStatus = Field(
        description=(
            "confirmed: the problem is real and your evidence shows it. "
            "refuted: you checked and the code is fine. "
            "inconclusive: what you saw isn't enough to decide."
        )
    )
    claim: str = Field(description="One sentence: the problem, or what you checked.")
    explanation: str = Field(
        description=(
            "confirmed: what goes wrong, when, and the impact. "
            "refuted or inconclusive: why, in a few sentences."
        )
    )
    category: Category
    severity: Severity = Field(description="How serious the problem is, or would be if real.")
    confidence: Confidence
    evidence: list[EvidenceRef] = Field(
        default_factory=list,
        description=(
            "Observations that support the verdict. A confirmed verdict needs at least one "
            "defect_site."
        ),
    )
    suggested_fix: str | None = Field(default=None, description="Optional: a short fix.")


class CheckedVerdict(BaseModel):
    """An accepted verdict, with its evidence verified and re-read from the repository."""

    verdict: Verdict
    evidence: list[Evidence]
    anchor: Anchor | None = Field(description="Where a confirmed problem's comment goes")


class SubmitVerdict:
    name = "submit_verdict"

    def __init__(self, workspace: Workspace, diff: ParsedDiff) -> None:
        self.workspace = workspace
        self.diff = diff

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=(
                "Finish the investigation with your verdict. Cite evidence by the observation "
                "ids listed above tool results; only lines you were shown can be cited."
            ),
            parameters=Verdict.model_json_schema(),
        )

    def check(
        self, arguments: dict[str, Any], ledger: ObservationLedger
    ) -> Submission[CheckedVerdict]:
        try:
            verdict = Verdict.model_validate(arguments)
        except ValidationError as exc:
            return Submission(errors=_problems(exc))
        result = check_evidence(
            verdict.evidence,
            ledger,
            self.workspace,
            self.diff,
            require_defect_site=verdict.status is VerdictStatus.CONFIRMED,
        )
        if not result.ok:
            return Submission(errors=result.errors)
        return Submission(
            value=CheckedVerdict(verdict=verdict, evidence=result.evidence, anchor=result.anchor)
        )


def outcome_status(result: LoopResult[CheckedVerdict]) -> VerdictStatus:
    """What the investigation concluded: the accepted verdict's status, otherwise inconclusive."""
    if result.submission is None:
        return VerdictStatus.INCONCLUSIVE
    return result.submission.verdict.status


def _problems(exc: ValidationError) -> list[str]:
    errors = exc.errors()
    problems = [
        f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
        for error in errors[:MAX_VALIDATION_PROBLEMS]
    ]
    if len(errors) > MAX_VALIDATION_PROBLEMS:
        problems.append(f"and {len(errors) - MAX_VALIDATION_PROBLEMS} more problems")
    return problems
