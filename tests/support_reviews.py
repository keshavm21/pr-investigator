"""A ready-made review of a GitHub pull request, for the publisher and web tests."""

from pr_investigator.agent.models import AgentBudget, AgentReview
from pr_investigator.domain import (
    Anchor,
    Category,
    Confidence,
    DataClass,
    Evidence,
    EvidenceRole,
    Finding,
    PullRequestRef,
    ReviewTarget,
    Severity,
    Side,
)
from pr_investigator.review.baseline import Coverage

HEAD = "a" * 40
BASE = "b" * 40

TARGET = ReviewTarget(
    title="Add <b>search</b>",
    description="",
    base_sha=BASE,
    head_sha=HEAD,
    merge_base_sha=BASE,
    data_class=DataClass.TEST,
    source="https://github.com/acme/app/pull/7",
    pull_request=PullRequestRef(owner="acme", repo="app", number=7),
)


def finding(id: str = "F1", **fields: object) -> Finding:
    base: dict[str, object] = {
        "id": id,
        "title": f"Problem {id}",
        "category": Category.SECURITY,
        "severity": Severity.HIGH,
        "confidence": Confidence.HIGH,
        "anchor": Anchor(path="app/a.py", side=Side.RIGHT, start_line=3, end_line=4),
        "explanation": f"Explanation {id}.",
        "anchor_in_diff": True,
        "evidence": [
            Evidence(
                role=EvidenceRole.DEFECT_SITE,
                observation_id="O1",
                note="here",
                ref="head",
                path="app/a.py",
                start_line=3,
                end_line=4,
                snippet="<script>alert(1)</script>",
            )
        ],
    }
    return Finding(**{**base, **fields})  # type: ignore[arg-type]


def make_review(*findings: Finding, posted: list[str] | None = None) -> AgentReview:
    found = list(findings) or [finding("F1"), finding("F2", title="Second")]
    return AgentReview(
        reviewer="a2",
        target=TARGET,
        summary="Summary <img src=x>.",
        findings=found,
        coverage=Coverage(skipped={"big.lock": "lockfile"}),
        provider="fake",
        model="m",
        prompts={},
        budget=AgentBudget(),
        posted_ids=[f.id for f in found] if posted is None else posted,
    )
