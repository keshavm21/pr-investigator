"""Per-case scores and aggregate metrics. Definitions are in docs/evaluation.md §2.1."""

from pydantic import BaseModel, Field

from pr_investigator.domain import Finding
from pr_investigator.evaluation.cases import CaseKind
from pr_investigator.evaluation.matching import MatchRecord, Verdict
from pr_investigator.llm.types import Usage


class CaseScore(BaseModel):
    case_id: str
    kind: CaseKind
    sample: int
    findings: list[Finding] = Field(default_factory=list)
    matches: list[MatchRecord] = Field(default_factory=list)
    expected_total: int = 0
    decoy_hits: list[str] = Field(default_factory=list)
    review_usage: Usage = Field(default_factory=Usage)
    judge_usage: Usage = Field(default_factory=Usage)
    cached: bool = False
    duration_seconds: float = 0.0
    error: str | None = None

    @property
    def true_positives(self) -> int:
        return len(self.matches)

    @property
    def false_positives(self) -> int:
        return len(self.findings) - len(self.matches)

    @property
    def missed(self) -> int:
        return self.expected_total - len(self.matches)


class Summary(BaseModel):
    runs: int
    failed_runs: int
    findings: int
    true_positives: int
    partial_matches: int
    false_positives: int
    expected_issues: int
    precision: float | None
    recall: float | None
    f0_5: float | None
    false_positives_per_clean_run: float | None
    decoy_trigger_rate: float | None
    anchor_in_diff_rate: float | None
    exact_location_rate: float | None
    input_tokens: int
    output_tokens: int
    cached_runs: int


def summarize(scores: list[CaseScore]) -> Summary:
    ok = [s for s in scores if s.error is None]
    findings = sum(len(s.findings) for s in ok)
    tp = sum(s.true_positives for s in ok)
    fp = sum(s.false_positives for s in ok)
    expected = sum(s.expected_total for s in ok)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / expected if expected else None
    clean = [s for s in ok if s.kind is CaseKind.CLEAN]
    decoys = [s for s in ok if s.kind is CaseKind.DECOY]
    all_findings = [f for s in ok for f in s.findings]
    all_matches = [m for s in ok for m in s.matches]
    usage = Usage()
    for s in ok:
        usage = usage + s.review_usage + s.judge_usage
    return Summary(
        runs=len(scores),
        failed_runs=len(scores) - len(ok),
        findings=findings,
        true_positives=tp,
        partial_matches=sum(1 for m in all_matches if m.verdict is Verdict.PARTIAL),
        false_positives=fp,
        expected_issues=expected,
        precision=precision,
        recall=recall,
        f0_5=_f_beta(precision, recall, 0.5),
        false_positives_per_clean_run=(
            sum(s.false_positives for s in clean) / len(clean) if clean else None
        ),
        decoy_trigger_rate=(
            sum(1 for s in decoys if s.decoy_hits) / len(decoys) if decoys else None
        ),
        anchor_in_diff_rate=(
            sum(1 for f in all_findings if f.anchor_in_diff) / len(all_findings)
            if all_findings
            else None
        ),
        exact_location_rate=(
            sum(1 for m in all_matches if m.distance == 0) / len(all_matches)
            if all_matches
            else None
        ),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_runs=sum(1 for s in ok if s.cached),
    )


def _f_beta(precision: float | None, recall: float | None, beta: float) -> float | None:
    if precision is None or recall is None:
        return None
    if precision == 0 and recall == 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * precision * recall / (b2 * precision + recall)
