"""Evaluation cases, materialization, matching and metrics."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from pr_investigator.domain import Anchor, Category, Confidence, Finding, Severity, Side
from pr_investigator.errors import EvalCaseError
from pr_investigator.evaluation.cases import CaseKind, EvalCase, load_cases
from pr_investigator.evaluation.matching import MatchRecord, Verdict, assign, decoy_hits, match
from pr_investigator.evaluation.materialize import MaterializedCase, Region, materialize
from pr_investigator.evaluation.metrics import CaseScore, summarize
from pr_investigator.evaluation.runner import validate_case
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.fake import ScriptedAdapter
from pr_investigator.workspace import resolve_commit
from tests.support import EVAL_CASES, EVAL_FIXTURES


def finding(
    fid: str, path: str, start: int, end: int | None = None, side: Side = Side.RIGHT
) -> Finding:
    return Finding(
        id=fid,
        title="t",
        category=Category.SECURITY,
        severity=Severity.HIGH,
        confidence=Confidence.HIGH,
        anchor=Anchor(path=path, side=side, start_line=start, end_line=end or start),
        explanation="e",
        anchor_in_diff=True,
    )


def test_suite_has_the_planned_mix_of_cases() -> None:
    cases = load_cases(EVAL_CASES)
    kinds = [c.kind for c in cases]
    assert len(cases) == 10
    assert kinds.count(CaseKind.SEEDED) == 6
    assert kinds.count(CaseKind.DECOY) == 2
    assert kinds.count(CaseKind.CLEAN) == 2


@pytest.mark.parametrize("case", load_cases(EVAL_CASES), ids=lambda c: c.id)
def test_every_case_materializes_and_its_regions_are_on_diff_lines(
    case: EvalCase, tmp_path: Path
) -> None:
    validate_case(case, EVAL_FIXTURES, tmp_path)


def test_materialization_is_deterministic(tmp_path: Path) -> None:
    (case,) = load_cases(EVAL_CASES, only=["seeded-sql-injection-search"])
    first = materialize(case, EVAL_FIXTURES, tmp_path / "one")
    second = materialize(case, EVAL_FIXTURES, tmp_path / "two")
    assert resolve_commit("pr", cwd=first.repo) == resolve_commit("pr", cwd=second.repo)


def test_case_validation_rules() -> None:
    edit = [{"path": "a.py", "create": "x"}]
    with pytest.raises(ValidationError):
        EvalCase.model_validate({"id": "x", "kind": "seeded", "title": "t", "edits": edit})
    with pytest.raises(ValidationError):
        EvalCase.model_validate({"id": "x", "kind": "decoy", "title": "t", "edits": edit})
    with pytest.raises(ValidationError):
        EvalCase.model_validate(
            {"id": "x", "kind": "clean", "title": "t", "edits": [{"path": "a.py"}]}
        )


def test_unknown_case_ids_and_ambiguous_edits_are_errors(tmp_path: Path) -> None:
    with pytest.raises(EvalCaseError):
        load_cases(EVAL_CASES, only=["does-not-exist"])
    bad = EvalCase.model_validate(
        {
            "id": "bad",
            "kind": "clean",
            "title": "t",
            "edits": [{"path": "app/routes/items.py", "find": "@router", "replace": "x"}],
        }
    )
    with pytest.raises(EvalCaseError, match="times"):
        materialize(bad, EVAL_FIXTURES, tmp_path)


def fake_case(expected: dict[str, Region], decoys: list[Region] | None = None) -> MaterializedCase:
    decoys = decoys or []
    case = EvalCase.model_validate(
        {
            "id": "c",
            "kind": "seeded" if expected else "decoy",
            "title": "t",
            "edits": [{"path": "a.py", "create": "x"}],
            "must_not_flag": [
                {"location": {"path": r.path, "match": "x"}, "reason": "safe"} for r in decoys
            ],
            "expected": [
                {
                    "id": issue_id,
                    "category": "security",
                    "severity": "high",
                    "location": {"path": region.path, "match": "x"},
                    "description": f"issue {issue_id}",
                }
                for issue_id, region in expected.items()
            ],
        }
    )
    return MaterializedCase(case=case, repo=Path("."), expected=expected, must_not_flag=decoys)


async def test_location_matching_uses_tolerance_side_and_one_to_one_assignment() -> None:
    mcase = fake_case({"E1": Region("a.py", 10, 10), "E2": Region("a.py", 30, 31)})
    findings = [
        finding("F1", "a.py", 12),  # 2 lines away from E1: matches
        finding("F2", "a.py", 11),  # also near E1, closer: wins E1
        finding("F3", "a.py", 30, side=Side.LEFT),  # base-side anchor can't match head lines
        finding("F4", "b.py", 30),  # wrong file
        finding("F5", "a.py", 40),  # too far from E2
    ]
    matches, _usage = await match(findings, mcase, judge_client=None, tolerance=3)
    assert [(m.finding_id, m.issue_id, m.distance) for m in matches] == [("F2", "E1", 1)]


async def test_judge_verdicts_decide_matches() -> None:
    mcase = fake_case({"E1": Region("a.py", 10, 10)})
    adapter = ScriptedAdapter(
        [
            {"verdict": "no", "rationale": "different problem"},
            {"verdict": "partial", "rationale": "right place, wrong reason"},
        ]
    )
    findings = [finding("F1", "a.py", 10), finding("F2", "a.py", 11)]
    matches, _usage = await match(findings, mcase, judge_client=LLMClient(adapter))
    assert [(m.finding_id, m.verdict) for m in matches] == [("F2", Verdict.PARTIAL)]
    assert "issue E1" in adapter.requests[0].messages[0].text


def test_assignment_prefers_matches_over_partials() -> None:
    records = [
        MatchRecord(finding_id="F1", issue_id="E1", distance=0, verdict=Verdict.PARTIAL),
        MatchRecord(finding_id="F2", issue_id="E1", distance=3, verdict=Verdict.MATCH),
    ]
    assert [r.finding_id for r in assign(records)] == ["F2"]


def test_decoy_hits() -> None:
    mcase = fake_case({}, decoys=[Region("a.py", 20, 20)])
    assert decoy_hits([finding("F1", "a.py", 21), finding("F2", "a.py", 40)], mcase, 3) == ["F1"]


def test_summary_metrics() -> None:
    seeded = CaseScore(
        case_id="s",
        kind=CaseKind.SEEDED,
        sample=0,
        findings=[finding("F1", "a.py", 1), finding("F2", "a.py", 9, 12)],
        matches=[MatchRecord(finding_id="F1", issue_id="E1", distance=0, verdict=Verdict.MATCH)],
        expected_total=2,
    )
    clean = CaseScore(
        case_id="c", kind=CaseKind.CLEAN, sample=0, findings=[finding("F1", "b.py", 1)]
    )
    decoy = CaseScore(case_id="d", kind=CaseKind.DECOY, sample=0, decoy_hits=["F9"])
    failed = CaseScore(case_id="x", kind=CaseKind.SEEDED, sample=0, error="boom", expected_total=1)
    summary = summarize([seeded, clean, decoy, failed])
    assert summary.precision == pytest.approx(1 / 3)
    assert summary.recall == pytest.approx(1 / 2)
    assert summary.f0_5 == pytest.approx((1.25 * (1 / 3) * 0.5) / (0.25 * (1 / 3) + 0.5))
    assert summary.false_positives_per_clean_run == 1.0
    assert summary.decoy_trigger_rate == 1.0
    assert summary.failed_runs == 1 and summary.exact_location_rate == 1.0
