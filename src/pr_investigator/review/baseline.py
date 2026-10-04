"""Baseline reviewer B0: one LLM call that sees only the PR's diff.

It's deliberately simple. It exists to be measured against, so later phases can show what
investigation, retrieval and verification add.
"""

import hashlib
import time
from fnmatch import fnmatch

from pydantic import BaseModel, Field

from pr_investigator.diff import FileDiff, ParsedDiff, render_file
from pr_investigator.domain import (
    Anchor,
    Category,
    Confidence,
    Finding,
    ReviewTarget,
    Severity,
    Side,
)
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.prompts import load_prompt
from pr_investigator.llm.types import Effort, LLMRequest, Message, Usage

STAGE = "baseline_review"
PROMPT_NAME, PROMPT_VERSION = "baseline_review", 1
REVIEWER = "b0"

# Files a human reviewer would skip too. Phase 1 uses name patterns only.
_SKIP_PATTERNS = {
    "lockfile": (
        "*.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "npm-shrinkwrap.json",
        "go.sum",
    ),
    "generated or vendored file": (
        "*.min.js",
        "*.min.css",
        "*_pb2.py",
        "*.pb.go",
        "vendor/*",
        "*/vendor/*",
        "node_modules/*",
        "*/node_modules/*",
        "dist/*",
    ),
}


class BaselineFindingOut(BaseModel):
    title: str = Field(description="One-line summary of the problem")
    category: Category
    severity: Severity
    confidence: Confidence
    path: str = Field(description="File path exactly as shown in the diff headers")
    side: Side = Field(description="RIGHT for added/unchanged lines, LEFT for deleted lines")
    start_line: int
    end_line: int
    explanation: str = Field(description="What is wrong, how it is triggered, and its impact")
    suggested_fix: str | None = Field(description="Optional short description of a fix")


class BaselineOutput(BaseModel):
    summary: str = Field(description="One or two sentences on what the pull request changes")
    findings: list[BaselineFindingOut]


class Coverage(BaseModel):
    reviewed: list[str] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict, description="path -> reason")


class ReviewResult(BaseModel):
    reviewer: str = REVIEWER
    target: ReviewTarget
    summary: str
    findings: list[Finding]
    coverage: Coverage
    provider: str
    model: str
    prompt_id: str
    prompt_sha256: str
    usage: Usage = Field(default_factory=Usage)
    attempts: int = 0
    cached: bool = False
    duration_seconds: float = 0.0


def skip_reason(file: FileDiff) -> str | None:
    if file.is_binary:
        return "binary file"
    if file.is_submodule:
        return "submodule"
    for reason, patterns in _SKIP_PATTERNS.items():
        if any(fnmatch(file.path, pattern) for pattern in patterns):
            return reason
    if not file.hunks:
        return "no content changes"
    return None


def render_diff(diff: ParsedDiff, max_chars: int) -> tuple[str, Coverage]:
    """Render reviewable files until the character budget is spent."""
    coverage = Coverage()
    parts: list[str] = []
    used = 0
    for file in diff.files:
        reason = skip_reason(file)
        if reason is not None:
            coverage.skipped[file.path] = reason
            continue
        rendered = render_file(file)
        if used + len(rendered) > max_chars:
            coverage.skipped[file.path] = "diff too large for one request"
            continue
        parts.append(rendered)
        used += len(rendered) + 2
        coverage.reviewed.append(file.path)
    return "\n\n".join(parts), coverage


def untrusted(label: str, content: str) -> str:
    """Wrap untrusted text in tags its author can't forge: the tag includes the content's hash."""
    digest = hashlib.sha256(content.encode("utf-8", errors="surrogateescape")).hexdigest()[:12]
    tag = f"untrusted-{label}-{digest}"
    return f"<{tag}>\n{content}\n</{tag}>"


def build_request(
    target: ReviewTarget, diff: ParsedDiff, *, max_diff_chars: int, sample: int = 0
) -> tuple[LLMRequest, Coverage]:
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    diff_text, coverage = render_diff(diff, max_diff_chars)
    metadata = f"Title: {target.title}\n\nDescription:\n{target.description or '(none)'}"
    lines = [
        "Review this pull request.",
        "",
        untrusted("pr", metadata),
        "",
        f"Changed files: {len(diff.files)}. Shown below: {len(coverage.reviewed)}.",
    ]
    if coverage.skipped:
        lines.append("Not shown:")
        lines += [f"- {path} ({reason})" for path, reason in coverage.skipped.items()]
    lines += ["", untrusted("diff", diff_text)]
    request = LLMRequest(
        stage=STAGE,
        data_class=target.data_class,
        system=prompt.text,
        messages=[Message.user("\n".join(lines))],
        output_schema=BaselineOutput,
        effort=Effort.MEDIUM,
        sample=sample,
    )
    return request, coverage


async def review_baseline(
    target: ReviewTarget,
    diff: ParsedDiff,
    client: LLMClient,
    *,
    max_diff_chars: int,
    sample: int = 0,
) -> ReviewResult:
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    request, coverage = build_request(target, diff, max_diff_chars=max_diff_chars, sample=sample)
    result = ReviewResult(
        target=target,
        summary="No reviewable changes.",
        findings=[],
        coverage=coverage,
        provider=client.provider,
        model=client.model,
        prompt_id=prompt.id,
        prompt_sha256=prompt.sha256,
    )
    if not coverage.reviewed:
        return result  # nothing to send, so spend no quota
    started = time.monotonic()
    response = await client.generate(request)
    assert isinstance(response.parsed, BaselineOutput)
    findings = [
        to_finding(index, out, diff) for index, out in enumerate(response.parsed.findings, 1)
    ]
    return result.model_copy(
        update={
            "summary": response.parsed.summary,
            "findings": findings,
            "usage": response.usage,
            "attempts": response.attempts,
            "cached": response.cached,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    )


def to_finding(index: int, out: BaselineFindingOut, diff: ParsedDiff) -> Finding:
    path = normalize_path(out.path, diff)
    start, end = sorted((max(out.start_line, 1), max(out.end_line, 1)))
    return Finding(
        id=f"F{index}",
        title=out.title,
        category=out.category,
        severity=out.severity,
        confidence=out.confidence,
        anchor=Anchor(path=path, side=out.side, start_line=start, end_line=end),
        explanation=out.explanation,
        suggested_fix=out.suggested_fix,
        anchor_in_diff=diff.is_commentable(path, out.side, start, end),
    )


def normalize_path(path: str, diff: ParsedDiff) -> str:
    """Models sometimes add git's a/ or b/ prefixes, or a leading ./ or /."""
    if diff.file(path) is not None:
        return path
    for prefix in ("b/", "a/", "./", "/"):
        candidate = path.removeprefix(prefix)
        if candidate != path and diff.file(candidate) is not None:
            return candidate
    return path
