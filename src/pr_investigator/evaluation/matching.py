"""Match reported findings to ground truth.

1. Candidate pairs: a finding and an expected issue in the same file whose line ranges overlap,
   or are within `tolerance` lines of each other (RIGHT-side anchors only; ground truth is in
   the head version).
2. Optional LLM judge: decides whether each candidate describes the same issue. Without a
   judge, location alone decides (cheaper, but credits any finding near the right lines).
3. One-to-one assignment, preferring judged matches, then the closest pairs.
"""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

from pr_investigator.domain import DataClass, Finding, Side
from pr_investigator.evaluation.cases import ExpectedIssue
from pr_investigator.evaluation.materialize import MaterializedCase, Region
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.prompts import load_prompt
from pr_investigator.llm.types import LLMRequest, Message, Usage
from pr_investigator.review.baseline import untrusted

JUDGE_STAGE = "eval_judge"
JUDGE_PROMPT = ("judge", 1)
DEFAULT_TOLERANCE = 3


class Verdict(StrEnum):
    MATCH = "match"
    PARTIAL = "partial"
    NO = "no"


class JudgeOutput(BaseModel):
    verdict: Verdict
    rationale: str = Field(description="One or two sentences explaining the verdict")


class MatchRecord(BaseModel):
    finding_id: str
    issue_id: str
    distance: int
    verdict: Verdict
    rationale: str | None = None


@dataclass(frozen=True)
class Candidate:
    finding: Finding
    issue: ExpectedIssue
    distance: int


def line_distance(finding: Finding, region: Region) -> int:
    """0 if the ranges overlap, otherwise the number of lines between them."""
    a, b = finding.anchor, region
    if a.end_line < b.start_line:
        return b.start_line - a.end_line
    if b.end_line < a.start_line:
        return a.start_line - b.end_line
    return 0


def near(finding: Finding, region: Region, tolerance: int) -> int | None:
    if finding.anchor.side is not Side.RIGHT or finding.anchor.path != region.path:
        return None
    distance = line_distance(finding, region)
    return distance if distance <= tolerance else None


def candidates(findings: list[Finding], mcase: MaterializedCase, tolerance: int) -> list[Candidate]:
    pairs: list[Candidate] = []
    for issue in mcase.case.expected:
        region = mcase.expected[issue.id]
        for finding in findings:
            distance = near(finding, region, tolerance)
            if distance is not None:
                pairs.append(Candidate(finding, issue, distance))
    return pairs


async def judge(
    client: LLMClient, candidate: Candidate, region: Region, *, sample: int = 0
) -> tuple[JudgeOutput, Usage]:
    prompt = load_prompt(*JUDGE_PROMPT)
    issue, finding = candidate.issue, candidate.finding
    truth = (
        f"Known issue ({issue.category.value}, {issue.severity.value}) at "
        f"{region.path}:{region.start_line}-{region.end_line}\n{issue.description}"
    )
    reported = (
        f"Finding ({finding.category.value}, {finding.severity.value}) at "
        f"{finding.anchor.path}:{finding.anchor.start_line}-{finding.anchor.end_line}\n"
        f"Title: {finding.title}\n{finding.explanation}"
    )
    request = LLMRequest(
        stage=JUDGE_STAGE,
        data_class=DataClass.TEST,
        system=prompt.text,
        messages=[Message.user(f"{truth}\n\n{untrusted('finding', reported)}")],
        output_schema=JudgeOutput,
        max_output_tokens=2_048,
        sample=sample,
    )
    response = await client.generate(request)
    assert isinstance(response.parsed, JudgeOutput)
    return response.parsed, response.usage


async def match(
    findings: list[Finding],
    mcase: MaterializedCase,
    *,
    judge_client: LLMClient | None,
    tolerance: int = DEFAULT_TOLERANCE,
    sample: int = 0,
) -> tuple[list[MatchRecord], Usage]:
    judged: list[MatchRecord] = []
    usage = Usage()
    for pair in candidates(findings, mcase, tolerance):
        if judge_client is None:
            verdict, rationale = Verdict.MATCH, "location-only matching"
        else:
            output, judge_usage = await judge(
                judge_client, pair, mcase.expected[pair.issue.id], sample=sample
            )
            usage = usage + judge_usage
            verdict, rationale = output.verdict, output.rationale
        judged.append(
            MatchRecord(
                finding_id=pair.finding.id,
                issue_id=pair.issue.id,
                distance=pair.distance,
                verdict=verdict,
                rationale=rationale,
            )
        )
    return assign(judged), usage


def assign(records: list[MatchRecord]) -> list[MatchRecord]:
    """Greedy one-to-one assignment: matches before partials, closer pairs first."""
    rank = {Verdict.MATCH: 0, Verdict.PARTIAL: 1}
    ordered = sorted(
        (r for r in records if r.verdict is not Verdict.NO),
        key=lambda r: (rank[r.verdict], r.distance, r.finding_id, r.issue_id),
    )
    used_findings: set[str] = set()
    used_issues: set[str] = set()
    chosen: list[MatchRecord] = []
    for record in ordered:
        if record.finding_id in used_findings or record.issue_id in used_issues:
            continue
        chosen.append(record)
        used_findings.add(record.finding_id)
        used_issues.add(record.issue_id)
    return chosen


def decoy_hits(findings: list[Finding], mcase: MaterializedCase, tolerance: int) -> list[str]:
    """Ids of findings that land on a must-not-flag region."""
    return [
        f.id
        for f in findings
        if any(near(f, region, tolerance) is not None for region in mcase.must_not_flag)
    ]
