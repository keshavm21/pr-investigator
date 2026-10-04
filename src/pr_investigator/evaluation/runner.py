"""Run a reviewer over evaluation cases, score it, and write the results.

Each case is materialized as a real git repository and reviewed through the same ingestion
path as a local PR. Repeated samples of a case use distinct cache entries (`sample`), so
re-running an eval replays every sample from the cache instead of spending quota.
"""

import json
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from pr_investigator import ingest
from pr_investigator.domain import DataClass, ReviewTarget, Side
from pr_investigator.errors import EvalCaseError, PRIError
from pr_investigator.evaluation.cases import EvalCase
from pr_investigator.evaluation.matching import DEFAULT_TOLERANCE, decoy_hits, match
from pr_investigator.evaluation.materialize import (
    BASE_BRANCH,
    HEAD_BRANCH,
    MaterializedCase,
    Region,
    materialize,
)
from pr_investigator.evaluation.metrics import CaseScore, Summary, summarize
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.types import LLMQuotaExhaustedError, RequestBudgetExceededError
from pr_investigator.review.baseline import PROMPT_NAME, PROMPT_VERSION, review_baseline
from pr_investigator.workspace import Workspace

Progress = Callable[[str], None]


class EvalRunInfo(BaseModel):
    reviewer: str = "b0"
    started_at: str
    provider: str
    model: str
    judge: str
    samples: int
    tolerance: int
    review_prompt: str
    cases: list[str]
    stopped_early: str | None = None


def prepare_case(
    case: EvalCase, fixtures_dir: Path, work_dir: Path
) -> tuple[MaterializedCase, ReviewTarget, Workspace]:
    mcase = materialize(case, fixtures_dir, work_dir / "repos")
    target, workspace = ingest.from_local(
        mcase.repo,
        BASE_BRANCH,
        HEAD_BRANCH,
        title=case.title,
        description=case.description,
        data_class=DataClass.TEST,
        workspaces_dir=work_dir / "workspaces",
    )
    return mcase, target, workspace


def validate_case(case: EvalCase, fixtures_dir: Path, work_dir: Path) -> None:
    """Check a case end to end without any LLM call: edits apply, locations resolve, and every
    ground-truth region sits on commentable lines of the PR diff."""
    mcase, _target, workspace = prepare_case(case, fixtures_dir, work_dir)
    diff = workspace.diff()
    regions: list[tuple[str, Region]] = [(f"expected {k}", r) for k, r in mcase.expected.items()]
    regions += [("must_not_flag", r) for r in mcase.must_not_flag]
    for label, region in regions:
        if not diff.is_commentable(region.path, Side.RIGHT, region.start_line, region.end_line):
            raise EvalCaseError(
                f"{case.id}: {label} region {region.path}:{region.start_line}-{region.end_line} "
                "is not on lines the PR diff shows"
            )


async def run_eval(
    cases: list[EvalCase],
    *,
    fixtures_dir: Path,
    client: LLMClient,
    judge_client: LLMClient | None,
    samples: int,
    max_diff_chars: int,
    tolerance: int = DEFAULT_TOLERANCE,
    progress: Progress | None = None,
) -> tuple[EvalRunInfo, list[CaseScore], Summary]:
    say = progress or (lambda _message: None)
    info = EvalRunInfo(
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        provider=client.provider,
        model=client.model,
        judge=f"{judge_client.provider}/{judge_client.model}" if judge_client else "location-only",
        samples=samples,
        tolerance=tolerance,
        review_prompt=f"{PROMPT_NAME}.v{PROMPT_VERSION}",
        cases=[c.id for c in cases],
    )
    scores: list[CaseScore] = []
    with tempfile.TemporaryDirectory(prefix="pri-eval-") as tmp:
        work_dir = Path(tmp)
        for case in cases:
            if info.stopped_early:
                break
            try:
                mcase, target, workspace = prepare_case(case, fixtures_dir, work_dir)
                diff = workspace.diff()
            except PRIError as exc:
                scores.append(CaseScore(case_id=case.id, kind=case.kind, sample=0, error=str(exc)))
                say(f"{case.id}: setup failed: {exc}")
                continue
            for sample in range(samples):
                score = CaseScore(
                    case_id=case.id,
                    kind=case.kind,
                    sample=sample,
                    expected_total=len(case.expected),
                )
                try:
                    result = await review_baseline(
                        target, diff, client, max_diff_chars=max_diff_chars, sample=sample
                    )
                    matches, judge_usage = await match(
                        result.findings,
                        mcase,
                        judge_client=judge_client,
                        tolerance=tolerance,
                        sample=sample,
                    )
                    score = score.model_copy(
                        update={
                            "findings": result.findings,
                            "matches": matches,
                            "decoy_hits": decoy_hits(result.findings, mcase, tolerance),
                            "review_usage": result.usage,
                            "judge_usage": judge_usage,
                            "cached": result.cached,
                            "duration_seconds": result.duration_seconds,
                        }
                    )
                    say(
                        f"{case.id} [sample {sample}]: {len(result.findings)} findings, "
                        f"{len(matches)}/{len(case.expected)} expected matched"
                        + (" (cached)" if result.cached else "")
                    )
                except (RequestBudgetExceededError, LLMQuotaExhaustedError) as exc:
                    score.error = str(exc)
                    info.stopped_early = str(exc)
                    say(f"stopping: {exc}")
                except PRIError as exc:
                    score.error = f"{type(exc).__name__}: {exc}"
                    say(f"{case.id} [sample {sample}]: failed: {exc}")
                scores.append(score)
                if info.stopped_early:
                    break
    return info, scores, summarize(scores)


def write_results(
    output_dir: Path, info: EvalRunInfo, scores: list[CaseScore], summary: Summary
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "results.jsonl").open("w", encoding="utf-8") as handle:
        for score in scores:
            handle.write(score.model_dump_json() + "\n")
    (output_dir / "summary.json").write_text(
        json.dumps(
            {"run": info.model_dump(mode="json"), "summary": summary.model_dump(mode="json")},
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(render_summary(info, scores, summary), encoding="utf-8")


def render_summary(info: EvalRunInfo, scores: list[CaseScore], summary: Summary) -> str:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    lines = [
        f"# Eval: {info.reviewer} on {info.model}",
        "",
        f"- Started: {info.started_at}",
        f"- Provider: {info.provider}; judge: {info.judge}; prompt: {info.review_prompt}",
        f"- Cases: {len(info.cases)}; samples per case: {info.samples}; "
        f"location tolerance: ±{info.tolerance} lines",
    ]
    if info.stopped_early:
        lines.append(f"- **Stopped early:** {info.stopped_early}")
    lines += [
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Precision | {pct(summary.precision)} |",
        f"| Recall | {pct(summary.recall)} |",
        f"| F0.5 | {pct(summary.f0_5)} |",
        f"| False positives per clean run | "
        f"{'n/a' if summary.false_positives_per_clean_run is None else f'{summary.false_positives_per_clean_run:.2f}'} |",  # noqa: E501
        f"| Decoy trigger rate | {pct(summary.decoy_trigger_rate)} |",
        f"| Anchors on diff lines | {pct(summary.anchor_in_diff_rate)} |",
        f"| Matches at the exact lines | {pct(summary.exact_location_rate)} |",
        f"| Findings (TP / partial / FP) | {summary.findings} "
        f"({summary.true_positives} / {summary.partial_matches} / {summary.false_positives}) |",
        f"| Runs (failed / served from cache) | {summary.runs} "
        f"({summary.failed_runs} / {summary.cached_runs}) |",
        f"| Tokens (in / out) | {summary.input_tokens} / {summary.output_tokens} |",
        "",
        "TP counts include partial matches. Unmatched findings count as false positives until a "
        "human adjudicates them.",
        "",
        "## Per case",
        "",
        "| Case | Kind | Sample | Findings | Matched | Decoy hits | Error |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        matched = f"{s.true_positives}/{s.expected_total}" if s.expected_total else "-"
        lines.append(
            f"| {s.case_id} | {s.kind.value} | {s.sample} | {len(s.findings)} | {matched} | "
            f"{len(s.decoy_hits) or '-'} | {s.error or ''} |"
        )
    return "\n".join(lines) + "\n"
