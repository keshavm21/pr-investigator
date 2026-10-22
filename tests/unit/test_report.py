"""The ablation table: intervals and the rendered report."""

import json
from pathlib import Path

from pr_investigator.evaluation.cases import CaseKind
from pr_investigator.evaluation.metrics import CaseScore
from pr_investigator.evaluation.report import (
    bootstrap,
    precision,
    recall,
    render_table,
    requests_per_true_positive,
)


def score(case_id: str, sample: int, tp: int, fp: int, expected: int) -> CaseScore:
    from pr_investigator.domain import Anchor, Category, Confidence, Finding, Severity, Side
    from pr_investigator.evaluation.matching import MatchRecord, Verdict

    findings = [
        Finding(
            id=f"F{i}",
            title="t",
            category=Category.CORRECTNESS,
            severity=Severity.HIGH,
            confidence=Confidence.HIGH,
            anchor=Anchor(path="a.py", side=Side.RIGHT, start_line=1, end_line=1),
            explanation="e",
            anchor_in_diff=True,
        )
        for i in range(tp + fp)
    ]
    matches = [
        MatchRecord(finding_id=f"F{i}", issue_id="I", verdict=Verdict.MATCH, distance=0)
        for i in range(tp)
    ]
    return CaseScore(
        case_id=case_id,
        kind=CaseKind.SEEDED if expected else CaseKind.CLEAN,
        sample=sample,
        findings=findings,
        matches=matches,
        expected_total=expected,
        requests=4,
    )


def test_metrics_and_interval_bracket_the_point_estimate() -> None:
    scores = [score(f"c{i}", s, tp=i % 2, fp=0, expected=1) for i in range(8) for s in range(2)]
    assert precision(scores) == 1.0
    assert recall(scores) == 0.5
    assert requests_per_true_positive(scores) == 8.0
    low, high = bootstrap(scores, recall) or (0.0, 0.0)
    assert low < 0.5 < high
    assert bootstrap(scores, recall) == bootstrap(scores, recall)  # seeded, so reproducible


def test_precision_is_undefined_without_findings() -> None:
    scores = [score("c", 0, tp=0, fp=0, expected=1)]
    assert precision(scores) is None
    assert bootstrap(scores, precision) is None


def test_render_table_reads_a_results_directory(tmp_path: Path) -> None:
    scores = [score(f"c{i}", 0, tp=1, fp=0, expected=1) for i in range(4)]
    (tmp_path / "results.jsonl").write_text(
        "\n".join(s.model_dump_json() for s in scores), encoding="utf-8"
    )
    (tmp_path / "summary.json").write_text(json.dumps({"run": {"samples": 1}}), encoding="utf-8")
    table = render_table([("X", tmp_path)])
    assert "| Test split | X |" in table
    assert "100% (100%-100%)" in table
    assert "4 (4 x 1)" in table
