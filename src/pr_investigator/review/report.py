"""Render review results and save them as run artifacts under the data directory."""

import secrets
from datetime import UTC, datetime
from pathlib import Path

from pr_investigator.domain import Finding, Severity
from pr_investigator.review.baseline import ReviewResult

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def sorted_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.anchor.path))


def render_markdown(result: ReviewResult) -> str:
    target = result.target
    lines = [
        f"# Review: {target.title}",
        "",
        f"- Source: {target.source}",
        f"- Commits: base `{target.base_sha[:12]}`, head `{target.head_sha[:12]}`",
        f"- Reviewer: {result.reviewer} ({result.provider} / {result.model}, "
        f"prompt {result.prompt_id})",
        f"- Tokens: {result.usage.input_tokens} in, {result.usage.output_tokens} out"
        + (" (from cache)" if result.cached else ""),
        "",
        f"**Summary:** {result.summary}",
        "",
    ]
    if not result.findings:
        lines.append("No findings.")
    for finding in sorted_findings(result.findings):
        anchor = finding.anchor
        where = f"{anchor.path}:{anchor.start_line}"
        if anchor.end_line != anchor.start_line:
            where += f"-{anchor.end_line}"
        if anchor.side.value == "LEFT":
            where += " (deleted lines)"
        flag = "" if finding.anchor_in_diff else " (anchor outside the diff)"
        lines += [
            f"## [{finding.severity.value}] {finding.title}",
            f"`{where}`{flag} · {finding.category.value} · confidence {finding.confidence.value}",
            "",
            finding.explanation,
        ]
        if finding.suggested_fix:
            lines += ["", f"**Suggested fix:** {finding.suggested_fix}"]
        lines.append("")
    if result.coverage.skipped:
        lines += ["## Not reviewed", ""]
        lines += [f"- {path}: {reason}" for path, reason in result.coverage.skipped.items()]
    return "\n".join(lines).rstrip() + "\n"


def write_run(result: ReviewResult, runs_dir: Path, run_id: str | None = None) -> Path:
    run_dir = runs_dir / (run_id or new_run_id())
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "run.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "review.md").write_text(render_markdown(result), encoding="utf-8")
    return run_dir
