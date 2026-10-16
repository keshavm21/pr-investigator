"""Per-case scores and aggregate metrics. Definitions are in docs/evaluation.md §2.1."""

from collections import Counter

from pydantic import BaseModel, Field

from pr_investigator.agent.models import AgentReview
from pr_investigator.domain import EvidenceRole, Finding
from pr_investigator.evaluation.cases import CaseKind
from pr_investigator.evaluation.matching import MatchRecord, Verdict
from pr_investigator.llm.types import Usage


class AgentStats(BaseModel):
    """How an agentic review went: what its investigations concluded and why they stopped."""

    suspects: int = 0  # A2: suspects investigated
    dropped_suspects: int = 0
    outcomes: dict[str, int] = Field(default_factory=dict)  # A2: confirmed / refuted / ...
    stop_causes: dict[str, int] = Field(default_factory=dict)  # per loop: submitted, ...
    turns: int = 0
    tool_calls: int = 0
    rejections: int = 0  # submissions the evidence check sent back
    duplicates: int = 0  # confirmed problems already reported by another investigation

    @classmethod
    def of(cls, review: AgentReview) -> "AgentStats":
        loops = [i.loop for i in review.investigations]
        if review.single is not None:
            loops.append(review.single)
        return cls(
            suspects=len(review.suspects),
            dropped_suspects=len(review.dropped_suspects),
            outcomes=dict(Counter(i.status.value for i in review.investigations)),
            stop_causes=dict(Counter(loop.cause.value for loop in loops)),
            turns=sum(loop.turns for loop in loops),
            tool_calls=sum(loop.tool_calls for loop in loops),
            rejections=sum(len(loop.rejections) for loop in loops),
            duplicates=sum(1 for i in review.investigations if i.duplicate_of),
        )

    def __add__(self, other: "AgentStats") -> "AgentStats":
        return AgentStats(
            suspects=self.suspects + other.suspects,
            dropped_suspects=self.dropped_suspects + other.dropped_suspects,
            outcomes=dict(Counter(self.outcomes) + Counter(other.outcomes)),
            stop_causes=dict(Counter(self.stop_causes) + Counter(other.stop_causes)),
            turns=self.turns + other.turns,
            tool_calls=self.tool_calls + other.tool_calls,
            rejections=self.rejections + other.rejections,
            duplicates=self.duplicates + other.duplicates,
        )


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
    requests: int = 0  # model requests for the review (cached ones included, judge excluded)
    cached_requests: int = 0
    agent: AgentStats | None = None  # agentic reviewers only
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
    requests: int = 0  # review model requests, cached ones included
    cached_requests: int = 0
    requests_per_run: float | None = None
    max_requests_per_run: int = 0
    agent: AgentStats | None = None  # totals, agentic reviewers only
    # Share of reported findings with a verified defect site (agentic reviewers; should be 1.0).
    verified_evidence_rate: float | None = None


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
        requests=sum(s.requests for s in ok),
        cached_requests=sum(s.cached_requests for s in ok),
        requests_per_run=sum(s.requests for s in ok) / len(ok) if ok else None,
        max_requests_per_run=max((s.requests for s in ok), default=0),
        agent=_agent_totals(ok),
        verified_evidence_rate=_verified_evidence_rate(ok),
    )


def _agent_totals(scores: list[CaseScore]) -> AgentStats | None:
    stats = [s.agent for s in scores if s.agent is not None]
    if not stats:
        return None
    total = AgentStats()
    for item in stats:
        total = total + item
    return total


def _verified_evidence_rate(scores: list[CaseScore]) -> float | None:
    findings = [f for s in scores if s.agent is not None for f in s.findings]
    if not findings:
        return None
    verified = sum(
        1 for f in findings if any(e.role is EvidenceRole.DEFECT_SITE for e in f.evidence)
    )
    return verified / len(findings)


def _f_beta(precision: float | None, recall: float | None, beta: float) -> float | None:
    if precision is None or recall is None:
        return None
    if precision == 0 and recall == 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * precision * recall / (b2 * precision + recall)
