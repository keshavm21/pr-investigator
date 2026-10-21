"""Aggregation: rank verified findings and apply the noise budget (D17).

Findings reach this step already deduplicated by anchor overlap (the reviewers drop a finding
whose lines another finding covers). Here they are ranked by severity times verification
strength, and a short list is chosen for posting by default.

Strength, weakest first: 1 = grounded (its evidence passed the mechanical check); +1 if a
mechanical lead points at its lines (corroboration); +2 if the skeptic upheld it. A refuted
finding is removed from the list and kept for display. An uncertain finding stays, at the
strength it had, and isn't posted by default. Self-reported confidence only breaks ties.
"""

from dataclasses import dataclass

from pr_investigator.agent.models import SkepticStatus, Verification
from pr_investigator.domain import Confidence, Finding, Severity
from pr_investigator.review.context import Lead

MAX_COMMENTS = 7
POSTABLE_SEVERITIES = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM)
_SEVERITY_WEIGHT = {Severity.CRITICAL: 4, Severity.HIGH: 3, Severity.MEDIUM: 2, Severity.LOW: 1}
_CONFIDENCE_WEIGHT = {Confidence.HIGH: 2, Confidence.MEDIUM: 1, Confidence.LOW: 0}


@dataclass
class Aggregation:
    kept: list[Finding]  # ranked, best first; refuted findings are not here
    refuted: list[Finding]
    posted_ids: list[str]  # the noise budget's choice, in rank order
    strengths: dict[str, int]


def corroborated(finding: Finding, leads: list[Lead]) -> bool:
    anchor = finding.anchor
    return any(
        lead.path == anchor.path
        and lead.line is not None
        and lead.side is anchor.side
        and anchor.start_line <= lead.line <= anchor.end_line
        for lead in leads
    )


def aggregate(
    findings: list[Finding],
    verifications: list[Verification],
    leads: list[Lead],
    *,
    max_comments: int = MAX_COMMENTS,
    verified: bool = True,
) -> Aggregation:
    """Rank findings and choose the ones to post.

    With `verified` (A3), a finding is postable only if the skeptic upheld it. Without a
    verifier (A1, A2), findings are ranked and capped the same way but carry no verification
    mark, and every kept finding of medium severity or higher is postable.
    """
    status = {v.finding_id: v.status for v in verifications}
    kept: list[Finding] = []
    refuted: list[Finding] = []
    strengths: dict[str, int] = {}
    for finding in findings:
        outcome = status.get(finding.id, SkepticStatus.UNCERTAIN)
        marked = finding.model_copy(update={"verification": outcome.value}) if verified else finding
        if outcome is SkepticStatus.REFUTED:
            refuted.append(marked)
            continue
        strengths[finding.id] = (
            1 + corroborated(finding, leads) + (2 if outcome is SkepticStatus.UPHELD else 0)
        )
        kept.append(marked)
    kept.sort(
        key=lambda f: (
            -_SEVERITY_WEIGHT[f.severity] * strengths[f.id],
            -_CONFIDENCE_WEIGHT[f.confidence],
            f.anchor.path,
            f.anchor.start_line,
        )
    )
    postable = [
        f.id
        for f in kept
        if f.severity in POSTABLE_SEVERITIES and (f.verification == "upheld" or not verified)
    ]
    return Aggregation(kept, refuted, postable[:max_comments], strengths)
