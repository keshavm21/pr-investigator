"""Render review results and save them as run artifacts under the data directory.

B0 and the agentic reviewers share the layout; agentic runs add evidence snippets and what each
investigation concluded. Evidence snippets are fenced so repository text can't end the block.
"""

import secrets
from datetime import UTC, datetime
from pathlib import Path

from pr_investigator.agent.models import AgentReview
from pr_investigator.domain import Evidence, EvidenceRole, Finding, Severity
from pr_investigator.review.baseline import ReviewResult
from pr_investigator.review.comments import render_review, scan_secrets
from pr_investigator.review.reviewers import AnyReview

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def sorted_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.anchor.path))


def render_markdown(result: AnyReview) -> str:
    target = result.target
    lines = [
        f"# Review: {target.title}",
        "",
        f"- Source: {target.source}",
        f"- Commits: base `{target.base_sha[:12]}`, head `{target.head_sha[:12]}`",
        _reviewer_line(result),
        _usage_line(result),
        "",
        f"**Summary:** {result.summary}",
        "",
    ]
    if not result.findings:
        lines.append("No findings.")
    ranked = isinstance(result, AgentReview) and result.reviewer in ("a1", "a2", "a3")
    for finding in result.findings if ranked else sorted_findings(result.findings):
        anchor = finding.anchor
        where = f"{anchor.path}:{anchor.start_line}"
        if anchor.end_line != anchor.start_line:
            where += f"-{anchor.end_line}"
        if anchor.side.value == "LEFT":
            where += " (deleted lines)"
        flag = "" if finding.anchor_in_diff else " (anchor outside the diff)"
        checked = f" · skeptic: {finding.verification}" if finding.verification else ""
        posted = ""
        if isinstance(result, AgentReview):
            posted = " · posted by default" if finding.id in result.posted_ids else " · not posted"
        lines += [
            f"## [{finding.severity.value}] {finding.title}",
            f"`{where}`{flag} · {finding.category.value} · confidence "
            f"{finding.confidence.value}{checked}{posted}",
            "",
            finding.explanation,
        ]
        if finding.suggested_fix:
            lines += ["", f"**Suggested fix:** {finding.suggested_fix}"]
        if finding.evidence:
            lines += ["", "**Evidence** (lines re-read from the analyzed commit):", ""]
            lines += [line for item in finding.evidence for line in _evidence(item)]
        lines.append("")
    if isinstance(result, AgentReview):
        lines += _verification(result)
        lines += _comments(result) if not result.verifications else []
        lines += _investigations(result)
    if result.coverage.skipped:
        lines += ["## Not reviewed", ""]
        lines += [f"- {path}: {reason}" for path, reason in result.coverage.skipped.items()]
    # Last pass over everything, model text included: a secret never reaches a report.
    return scan_secrets("\n".join(lines).rstrip() + "\n")[0]


def _reviewer_line(result: AnyReview) -> str:
    if isinstance(result, ReviewResult):
        prompts = f"prompt {result.prompt_id}"
    else:
        prompts = "prompts " + ", ".join(result.prompts)
    return f"- Reviewer: {result.reviewer} ({result.provider} / {result.model}, {prompts})"


def _usage_line(result: AnyReview) -> str:
    tokens = f"- Tokens: {result.usage.input_tokens} in, {result.usage.output_tokens} out"
    if isinstance(result, ReviewResult):
        return tokens + (" (from cache)" if result.cached else "")
    return tokens + f" · requests: {result.requests} ({result.cached_requests} from cache)"


def _evidence(item: Evidence) -> list[str]:
    if item.role is EvidenceRole.ABSENCE:
        return [f"- absence: the search {item.query} found nothing. {_one_line(item.note)}", ""]
    where = f"{item.path}:{item.start_line}-{item.end_line} ({item.ref})"
    snippet = item.snippet or ""
    fence = "`" * max(3, _longest_backtick_run(snippet) + 1)
    return [
        f"- {item.role.value} `{where}`: {_one_line(item.note)}",
        "",
        f"  {fence}",
        *[f"  {line}" for line in snippet.split("\n")],
        f"  {fence}",
        "",
    ]


def _verification(result: AgentReview) -> list[str]:
    if not result.verifications:
        return []
    lines = ["## Verification", ""]
    for v in result.verifications:
        strength = result.strengths.get(v.finding_id)
        extra = f", strength {strength}" if strength is not None else ""
        lines.append(f"- {v.finding_id}: {v.status.value}{extra}. {_one_line(v.explanation)}")
    lines.append("")
    if result.refuted:
        lines += ["## Refuted by the skeptic (not reported)", ""]
        for f in result.refuted:
            lines.append(f"- {_one_line(f.title)} (`{f.anchor.path}:{f.anchor.start_line}`)")
        lines.append("")
    lines += _comments(result)
    return lines


def _comments(result: AgentReview) -> list[str]:
    rendered = render_review(result)
    if not rendered.comments:
        return ["## Comments that would be posted", "", "None.", ""]
    lines = ["## Comments that would be posted", ""]
    for comment in rendered.comments:
        a = comment.anchor
        lines += [
            f"### {comment.finding_id} at `{a.path}:{a.start_line}-{a.end_line}` ({a.side.value})",
            "",
            *[f"> {line}" for line in comment.body.split("\n")],
            "",
        ]
    return lines


def _investigations(result: AgentReview) -> list[str]:
    lines: list[str] = []
    if result.investigations:
        lines += [
            "## Investigations",
            "",
            "| Suspect | Outcome | Ended | Turns | Finding |",
            "|---|---|---|---|---|",
        ]
        for inv in result.investigations:
            outcome = inv.status.value
            if inv.duplicate_of:
                outcome += f" (same as {inv.duplicate_of})"
            cells = [
                f"{inv.suspect.id}: {inv.suspect.title}",
                outcome,
                inv.loop.cause.value,
                str(inv.loop.turns),
                inv.finding_id or "",
            ]
            lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
        lines.append("")
        explained = [i for i in result.investigations if i.verdict and i.finding_id is None]
        for inv in explained:
            assert inv.verdict is not None
            why = _one_line(inv.verdict.verdict.explanation)
            lines.append(f"- {inv.suspect.id} ({inv.status.value}): {why}")
        if explained:
            lines.append("")
    if result.dropped_suspects:
        lines += ["## Suspects not investigated", ""]
        lines += [f"- {_one_line(d.suspect.title)}: {d.reason}" for d in result.dropped_suspects]
        lines.append("")
    if result.single is not None:
        loop = result.single
        lines += [
            "## Investigation",
            "",
            f"Ended: {loop.cause.value} after {loop.turns} turns and {loop.tool_calls} tool calls"
            f"; {len(loop.rejections)} rejected reports.",
            "",
        ]
    return lines


def _cell(text: str) -> str:
    return _one_line(text).replace("|", "\\|")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _longest_backtick_run(text: str) -> int:
    longest = current = 0
    for char in text:
        current = current + 1 if char == "`" else 0
        longest = max(longest, current)
    return longest


def create_run_dir(runs_dir: Path, run_id: str | None = None) -> Path:
    """A new, empty run directory. Created before the review, so a trace can be written into
    it as the review happens."""
    run_dir = runs_dir / (run_id or new_run_id())
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def write_run(result: AnyReview, runs_dir: Path, run_id: str | None = None) -> Path:
    run_dir = create_run_dir(runs_dir, run_id)
    save_run(result, run_dir)
    return run_dir


def save_run(result: AnyReview, run_dir: Path) -> None:
    (run_dir / "run.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "review.md").write_text(render_markdown(result), encoding="utf-8")
