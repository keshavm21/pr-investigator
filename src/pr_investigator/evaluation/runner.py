"""Run a reviewer over evaluation cases, score it, and write the results.

Each case is materialized as a real git repository and reviewed through the same ingestion
path as a local PR. Repeated samples of a case use distinct cache entries (`sample`), so
re-running an eval replays every sample from the cache instead of spending quota.

Agentic reviewers write one trace per case and sample (`traces/<case>-s<sample>.jsonl`). The run
record names the reviewer, its prompts and budget, and the commit of this checkout, so results
can be tied to the code that produced them.
"""

import json
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from pr_investigator import ingest
from pr_investigator.agent.models import AgentBudget, AgentReview
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
from pr_investigator.evaluation.metrics import AgentStats, CaseScore, Summary, summarize
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.types import LLMQuotaExhaustedError, RequestBudgetExceededError
from pr_investigator.review.reviewers import (
    AnyReview,
    Reviewer,
    check_toolset,
    reviewer_prompts,
    run_reviewer,
)
from pr_investigator.tools import Toolset
from pr_investigator.trace import Trace
from pr_investigator.workspace import Workspace, run_git

Progress = Callable[[str], None]


class EvalRunInfo(BaseModel):
    reviewer: str = "b0"
    started_at: str
    provider: str
    model: str
    judge: str
    samples: int
    tolerance: int
    review_prompt: str  # prompt ids, comma-separated
    cases: list[str]
    stopped_early: str | None = None
    prompts: dict[str, str] = Field(default_factory=dict, description="prompt id -> sha256")
    budget: AgentBudget | None = None  # agentic reviewers only
    code_commit: str | None = None  # this checkout of PR Investigator
    code_dirty: bool | None = None  # uncommitted changes when the run started
    provider_requests: int | None = None  # real provider calls, retries and judge included
    split: str = "all"  # which cases: dev, test or all
    toolset: str = "base"  # the agent's tools (a1 and a2)
    context: bool = False  # a1: context pack and leads in the opening message


def has_code_changes(porcelain: str) -> bool:
    """Whether `git status --porcelain` output shows changes other than eval result folders.

    Filtered here rather than with an exclude pathspec: run_git sets GIT_LITERAL_PATHSPECS,
    which turns pathspec magic such as ":(exclude)" into a literal path.
    """
    for line in porcelain.splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        if line.strip() and not path.startswith("evals/results/"):
            return True
    return False


def code_version() -> tuple[str | None, bool | None]:
    """The commit of this PR Investigator checkout, and whether it has uncommitted changes.
    (None, None) when the package isn't running from a git checkout."""
    root = Path(__file__).resolve().parents[3]
    if not (root / ".git").exists() or not (root / "pyproject.toml").is_file():
        return None, None
    try:
        commit = run_git(["rev-parse", "HEAD"], cwd=root).decode().strip()
        dirty = has_code_changes(run_git(["status", "--porcelain"], cwd=root).decode())
    except PRIError:
        return None, None
    return commit, dirty


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
    reviewer: Reviewer = Reviewer.B0,
    budget: AgentBudget | None = None,
    traces_dir: Path | None = None,
    split: str = "all",
    toolset: Toolset = Toolset.BASE,
    index_db: Path | None = None,
    context: bool = False,
) -> tuple[EvalRunInfo, list[CaseScore], Summary]:
    say = progress or (lambda _message: None)
    check_toolset(reviewer, toolset, context)
    budget = (budget or AgentBudget()) if reviewer.agentic else None
    prompts = reviewer_prompts(reviewer, toolset)
    commit, dirty = code_version()
    info = EvalRunInfo(
        reviewer=reviewer.value,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        provider=client.provider,
        model=client.model,
        judge=f"{judge_client.provider}/{judge_client.model}" if judge_client else "location-only",
        samples=samples,
        tolerance=tolerance,
        review_prompt=", ".join(p.id for p in prompts),
        cases=[c.id for c in cases],
        prompts={p.id: p.sha256 for p in prompts},
        budget=budget,
        code_commit=commit,
        code_dirty=dirty,
        split=split,
        toolset=toolset.value,
        context=context,
    )
    if traces_dir is not None and reviewer.agentic:
        traces_dir.mkdir(parents=True, exist_ok=True)
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
                trace_path = None
                if traces_dir is not None and reviewer.agentic:
                    trace_path = traces_dir / f"{case.id}-s{sample}.jsonl"
                try:
                    with Trace(trace_path) as trace:
                        result = await run_reviewer(
                            reviewer,
                            target,
                            workspace,
                            diff,
                            client,
                            max_diff_chars=max_diff_chars,
                            budget=budget,
                            sample=sample,
                            trace=trace,
                            toolset=toolset,
                            index_db=index_db,
                            context=context,
                        )
                    requests, cached_requests = request_counts(result)
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
                            "cached": requests > 0 and cached_requests == requests,
                            "requests": requests,
                            "cached_requests": cached_requests,
                            "agent": (
                                AgentStats.of(result) if isinstance(result, AgentReview) else None
                            ),
                            "duration_seconds": result.duration_seconds,
                        }
                    )
                    say(
                        f"{case.id} [sample {sample}]: {len(result.findings)} findings, "
                        f"{len(matches)}/{len(case.expected)} expected matched"
                        + (" (cached)" if score.cached else f", {requests} requests")
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


def request_counts(result: AnyReview) -> tuple[int, int]:
    """(model requests, of which served from the cache) for one review."""
    if isinstance(result, AgentReview):
        return result.requests, result.cached_requests
    return result.attempts, result.attempts if result.cached else 0


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
        f"- Cases: {len(info.cases)} (split: {info.split}); samples per case: {info.samples}; "
        f"location tolerance: ±{info.tolerance} lines",
    ]
    if info.reviewer in ("a1", "a2"):
        lines.append(
            f"- Tools: {info.toolset}; context pack and leads: {'on' if info.context else 'off'}"
        )
    if info.code_commit:
        dirty = " with uncommitted changes" if info.code_dirty else ""
        lines.append(f"- Code: commit `{info.code_commit[:12]}`{dirty}")
    if info.budget is not None:
        b = info.budget
        lines.append(
            f"- Budget: {b.max_suspects} suspects, {b.investigation_turns} turns per "
            f"investigation, {b.single_agent_turns} turns for A1, {b.max_tool_calls_per_turn} "
            f"tool calls per turn, {b.max_tool_output_chars} characters per tool result"
        )
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
        f"| Review requests (from cache) | {summary.requests} ({summary.cached_requests}) |",
        f"| Requests per run (mean / max) | "
        f"{'n/a' if summary.requests_per_run is None else f'{summary.requests_per_run:.1f}'} / "
        f"{summary.max_requests_per_run} |",
    ]
    if info.provider_requests is not None:
        lines.append(
            f"| Real provider requests (retries and judge included) | {info.provider_requests} |"
        )
    lines += [
        "",
        "TP counts include partial matches. Unmatched findings count as false positives until a "
        "human adjudicates them.",
        "",
    ]
    if summary.agent is not None:
        lines += _agent_section(summary)
    lines += [
        "## Per case",
        "",
        "| Case | Kind | Sample | Findings | Matched | Decoy hits | Requests | Error |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        matched = f"{s.true_positives}/{s.expected_total}" if s.expected_total else "-"
        requests = f"{s.requests}" + (" (cached)" if s.cached else "")
        lines.append(
            f"| {s.case_id} | {s.kind.value} | {s.sample} | {len(s.findings)} | {matched} | "
            f"{len(s.decoy_hits) or '-'} | {requests} | {s.error or ''} |"
        )
    return "\n".join(lines) + "\n"


def _agent_section(summary: Summary) -> list[str]:
    agent = summary.agent
    assert agent is not None

    def counts(values: dict[str, int]) -> str:
        return ", ".join(f"{name} {count}" for name, count in sorted(values.items())) or "none"

    rate = summary.verified_evidence_rate
    verified = "n/a" if rate is None else f"{rate:.0%}"
    return [
        "## Investigations",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Reported findings with verified evidence | {verified} |",
        f"| Suspects investigated (dropped) | {agent.suspects} ({agent.dropped_suspects}) |",
        f"| Outcomes | {counts(agent.outcomes)} |",
        f"| Why loops stopped | {counts(agent.stop_causes)} |",
        f"| Turns / tool calls | {agent.turns} / {agent.tool_calls} |",
        f"| Submissions rejected by the evidence check | {agent.rejections} |",
        f"| Duplicate confirmations | {agent.duplicates} |",
        f"| Provider retries | {agent.retries} |",
        "",
    ]
