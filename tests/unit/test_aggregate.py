"""Ranking and the noise budget."""

from pr_investigator.agent.loop import StopCause
from pr_investigator.agent.models import LoopRecord, SkepticStatus, Verification
from pr_investigator.domain import Anchor, Category, Confidence, Finding, Severity, Side
from pr_investigator.llm.types import Usage
from pr_investigator.review.aggregate import aggregate
from pr_investigator.review.context import Lead

LOOP = LoopRecord(
    cause=StopCause.SUBMITTED, turns=1, cached_turns=0, tool_calls=0, observations=0, usage=Usage()
)


def finding(
    id: str,
    severity: Severity = Severity.HIGH,
    line: int = 1,
    confidence: Confidence = Confidence.HIGH,
) -> Finding:
    return Finding(
        id=id,
        title=id,
        category=Category.CORRECTNESS,
        severity=severity,
        confidence=confidence,
        anchor=Anchor(path="a.py", start_line=line, end_line=line),
        explanation="e",
        anchor_in_diff=True,
    )


def verdict(id: str, status: SkepticStatus) -> Verification:
    return Verification(finding_id=id, status=status, explanation="x", loop=LOOP)


def test_ranking_multiplies_severity_by_verification_strength() -> None:
    findings = [
        finding("high-uncertain", Severity.HIGH),  # 3 * 1
        finding("medium-upheld", Severity.MEDIUM, 2),  # 2 * 3
        finding("critical-uncertain", Severity.CRITICAL, 3),  # 4 * 1
    ]
    verdicts = [
        verdict("high-uncertain", SkepticStatus.UNCERTAIN),
        verdict("medium-upheld", SkepticStatus.UPHELD),
        verdict("critical-uncertain", SkepticStatus.UNCERTAIN),
    ]
    result = aggregate(findings, verdicts, [])
    assert [f.id for f in result.kept] == ["medium-upheld", "critical-uncertain", "high-uncertain"]
    assert result.strengths == {"medium-upheld": 3, "critical-uncertain": 1, "high-uncertain": 1}


def test_a_lead_on_the_finding_lines_corroborates_it() -> None:
    f = finding("f", line=5)
    lead = Lead(path="a.py", text="t", line=5, side=Side.RIGHT)
    elsewhere = Lead(path="a.py", text="t", line=9, side=Side.RIGHT)
    other_side = Lead(path="a.py", text="t", line=5, side=Side.LEFT)
    uncertain = [verdict("f", SkepticStatus.UNCERTAIN)]
    assert aggregate([f], uncertain, [lead]).strengths["f"] == 2
    assert aggregate([f], uncertain, [elsewhere, other_side]).strengths["f"] == 1
    assert aggregate([f], [verdict("f", SkepticStatus.UPHELD)], [lead]).strengths["f"] == 4


def test_refuted_findings_leave_the_list_and_keep_their_mark() -> None:
    result = aggregate(
        [finding("a"), finding("b", line=2)], [verdict("a", SkepticStatus.REFUTED)], []
    )
    assert [f.id for f in result.kept] == ["b"]  # no verdict means uncertain: kept, not posted
    assert [f.id for f in result.refuted] == ["a"] and result.refuted[0].verification == "refuted"
    assert result.posted_ids == []


def test_only_upheld_findings_of_medium_or_higher_are_posted_up_to_the_budget() -> None:
    findings = [finding(f"f{i}", Severity.HIGH, i + 1) for i in range(9)]
    findings += [finding("low", Severity.LOW, 20), finding("unsure", Severity.CRITICAL, 21)]
    verdicts = [verdict(f.id, SkepticStatus.UPHELD) for f in findings if f.id != "unsure"]
    verdicts.append(verdict("unsure", SkepticStatus.UNCERTAIN))
    result = aggregate(findings, verdicts, [])
    assert len(result.posted_ids) == 7
    assert "low" not in result.posted_ids and "unsure" not in result.posted_ids
    assert aggregate(findings, verdicts, [], max_comments=2).posted_ids == ["f0", "f1"]


def test_confidence_breaks_ties_before_location() -> None:
    low = finding("low", confidence=Confidence.LOW, line=1)
    high = finding("high", confidence=Confidence.HIGH, line=9)
    result = aggregate([low, high], [], [])
    assert [f.id for f in result.kept] == ["high", "low"]


def test_without_a_verifier_medium_and_higher_findings_are_postable_and_unmarked() -> None:
    findings = [
        finding("a", Severity.HIGH, 1),
        finding("b", Severity.LOW, 2),
        finding("c", Severity.MEDIUM, 3),
    ]
    result = aggregate(findings, [], [], verified=False)
    assert [f.id for f in result.kept] == ["a", "c", "b"]
    assert result.posted_ids == ["a", "c"]
    assert all(f.verification is None for f in result.kept)
