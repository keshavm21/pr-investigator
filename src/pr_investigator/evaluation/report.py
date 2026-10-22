"""The ablation table: several result directories side by side, with confidence intervals.

The interval is a percentile bootstrap that resamples whole cases (every sample of a case moves
together), since the samples of one case are not independent. With 15 cases the intervals are wide;
that is the honest answer to "is A2 better than B1".
"""

import json
import random
from collections.abc import Callable, Sequence
from pathlib import Path

from pr_investigator.evaluation.metrics import CaseScore

BOOTSTRAP_ROUNDS = 2000
BOOTSTRAP_SEED = 7

Interval = tuple[float, float]
Metric = Callable[[Sequence[CaseScore]], float | None]


def load_scores(results_dir: Path) -> list[CaseScore]:
    lines = (results_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    return [CaseScore.model_validate(json.loads(line)) for line in lines if line.strip()]


def load_info(results_dir: Path) -> dict[str, object]:
    data = json.loads((results_dir / "summary.json").read_text(encoding="utf-8"))
    run = data["run"]
    assert isinstance(run, dict)
    return run


def precision(scores: Sequence[CaseScore]) -> float | None:
    tp = sum(s.true_positives for s in scores)
    found = sum(len(s.findings) for s in scores)
    return tp / found if found else None


def recall(scores: Sequence[CaseScore]) -> float | None:
    expected = sum(s.expected_total for s in scores)
    return sum(s.true_positives for s in scores) / expected if expected else None


def requests_per_true_positive(scores: Sequence[CaseScore]) -> float | None:
    tp = sum(s.true_positives for s in scores)
    return sum(s.requests for s in scores) / tp if tp else None


def input_tokens_per_true_positive(scores: Sequence[CaseScore]) -> float | None:
    tp = sum(s.true_positives for s in scores)
    return sum(s.review_usage.input_tokens for s in scores) / tp if tp else None


def bootstrap(scores: Sequence[CaseScore], metric: Metric) -> Interval | None:
    """95% percentile interval of ``metric``, resampling cases with all their samples."""
    by_case: dict[str, list[CaseScore]] = {}
    for score in scores:
        by_case.setdefault(score.case_id, []).append(score)
    groups = list(by_case.values())
    if not groups:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    values: list[float] = []
    for _ in range(BOOTSTRAP_ROUNDS):
        sample = [s for g in rng.choices(groups, k=len(groups)) for s in g]
        value = metric(sample)
        if value is not None:
            values.append(value)
    if len(values) < BOOTSTRAP_ROUNDS // 2:  # the metric is undefined on most resamples
        return None
    values.sort()
    return values[int(0.025 * len(values))], values[min(len(values) - 1, int(0.975 * len(values)))]


def _pct(value: float | None, interval: Interval | None) -> str:
    if value is None:
        return "n/a"
    text = f"{value:.0%}"
    return f"{text} ({interval[0]:.0%}-{interval[1]:.0%})" if interval else text


def _num(value: float | None, interval: Interval | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    text = f"{value:.{digits}f}"
    return f"{text} ({interval[0]:.{digits}f}-{interval[1]:.{digits}f})" if interval else text


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def render_table(columns: Sequence[tuple[str, Path]]) -> str:
    """Markdown ablation table, one column per ``(label, results directory)``."""
    rows: dict[str, list[str]] = {
        name: []
        for name in (
            "Runs (cases x samples)",
            "Precision (95% CI)",
            "Recall (95% CI)",
            "False positives per clean run",
            "Decoys flagged",
            "Requests per run (mean / max)",
            "Requests per true positive (95% CI)",
            "Input tokens per true positive (95% CI)",
            "Seconds per run, live runs only (median)",
        )
    }
    for _label, path in columns:
        scores = [s for s in load_scores(path) if s.error is None]
        info = load_info(path)
        cases = len({s.case_id for s in scores})
        clean = [s for s in scores if s.kind.value == "clean"]
        decoys = [s for s in scores if s.kind.value == "decoy"]
        live = sorted(s.duration_seconds for s in scores if s.cached_requests == 0)
        requests = [float(s.requests) for s in scores]
        rows["Runs (cases x samples)"].append(f"{len(scores)} ({cases} x {info.get('samples')})")
        rows["Precision (95% CI)"].append(_pct(precision(scores), bootstrap(scores, precision)))
        rows["Recall (95% CI)"].append(_pct(recall(scores), bootstrap(scores, recall)))
        rows["False positives per clean run"].append(
            f"{sum(s.false_positives for s in clean) / len(clean):.2f}" if clean else "n/a"
        )
        rows["Decoys flagged"].append(
            f"{sum(1 for s in decoys if s.decoy_hits)} of {len(decoys)}" if decoys else "n/a"
        )
        mean_requests = _mean(requests)
        rows["Requests per run (mean / max)"].append(
            f"{mean_requests:.1f} / {int(max(requests))}" if mean_requests is not None else "n/a"
        )
        rows["Requests per true positive (95% CI)"].append(
            _num(
                requests_per_true_positive(scores),
                bootstrap(scores, requests_per_true_positive),
            )
        )
        rows["Input tokens per true positive (95% CI)"].append(
            _num(
                input_tokens_per_true_positive(scores),
                bootstrap(scores, input_tokens_per_true_positive),
                digits=0,
            )
        )
        rows["Seconds per run, live runs only (median)"].append(
            f"{live[len(live) // 2]:.0f}" if live else "n/a (all cached)"
        )
    header = "| Test split | " + " | ".join(label for label, _ in columns) + " |"
    divider = "|---|" + "---|" * len(columns)
    body = [f"| {name} | " + " | ".join(cells) + " |" for name, cells in rows.items()]
    return "\n".join([header, divider, *body])
