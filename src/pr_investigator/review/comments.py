"""Comment rendering: from verified findings to the text that would be posted to GitHub.

Everything a model wrote is untrusted output (docs/security.md §5.4). Comments are built from
structured fields through one fixed template, then passed through the sanitizer and the secret
scanner. Nothing here posts anything: the publisher (Phase 5) is the only component that may,
and only for findings a human approved.

What the sanitizer guarantees: no raw HTML, no images, no links except permalinks into the
reviewed repository at the analyzed commit, no live @-mentions, no control or invisible
characters, and a length cap. The scanner redacts known secret formats and high-entropy tokens.
"""

import math
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import quote

from pr_investigator.agent.models import AgentReview
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import Anchor, Evidence, EvidenceRole, Finding, ReviewTarget

MAX_FIELD_CHARS = 1_500
MAX_BODY_CHARS = 4_000
REDACTED = "[redacted: looks like a secret]"
FOOTER = "<sub>Reported by PR Investigator (AI-assisted review).</sub>"

_HTML = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_URL = re.compile(r"(?:https?|ftp)://[^\s<>)\]]+|\bwww\.[^\s<>)\]]+", re.IGNORECASE)
_MENTION = re.compile(r"(?<![\w`/])@([A-Za-z0-9][A-Za-z0-9-]*(?:/[A-Za-z0-9_-]+)?)")
_KEEP_CONTROL = {"\n", "\t"}

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----)?", re.S),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),
)
_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
ENTROPY_THRESHOLD = 4.2  # bits per character; hex digests sit near 4, random base64 near 6


def shannon_entropy(text: str) -> float:
    counts = {c: text.count(c) for c in set(text)}
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _secretish(part: str) -> bool:
    return len(part) >= 32 and shannon_entropy(part) >= ENTROPY_THRESHOLD


def scan_secrets(text: str) -> tuple[str, int]:
    """The text with secrets redacted, and how many were found."""
    found = 0

    def hide(_match: re.Match[str]) -> str:
        nonlocal found
        found += 1
        return REDACTED

    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(hide, text)

    url_spans = [m.span() for m in _URL.finditer(text)]

    def in_url(match: re.Match[str]) -> bool:
        return any(start <= match.start() and match.end() <= end for start, end in url_spans)

    def maybe(match: re.Match[str]) -> str:
        token = match.group(0)
        if in_url(match) and "/" in token:
            # A permalink's path (owner/repo/blob/<sha>/file) blends into one long token whose
            # entropy says nothing; judge each path segment on its own.
            parts = token.split("/")
            for index, part in enumerate(parts):
                if _secretish(part):
                    parts[index] = hide(match)
            return "/".join(parts)
        return hide(match) if shannon_entropy(token) >= ENTROPY_THRESHOLD else token

    return _TOKEN.sub(maybe, text), found


def permalink_prefix(target: ReviewTarget) -> str | None:
    pr = target.pull_request
    return f"https://github.com/{pr.owner}/{pr.repo}/blob/" if pr is not None else None


def sanitize(text: str, target: ReviewTarget, *, max_chars: int = MAX_FIELD_CHARS) -> str:
    """Make untrusted text safe to put in a comment."""
    text = "".join(
        c for c in text if c in _KEEP_CONTROL or unicodedata.category(c) not in ("Cc", "Cf", "Cs")
    )
    text = _HTML.sub("", text)
    text = _IMAGE.sub("", text)
    prefix = permalink_prefix(target)

    def allowed(url: str) -> bool:
        return prefix is not None and url.startswith(prefix)

    text = _LINK.sub(lambda m: m.group(0) if allowed(m.group(2)) else m.group(1), text)
    text = _URL.sub(lambda m: m.group(0) if allowed(m.group(0)) else "[link removed]", text)
    text = _MENTION.sub(lambda m: f"`@{m.group(1)}`", text)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + " [cut]"
    return text.strip()


def resolve_anchor(finding: Finding, diff: ParsedDiff | None) -> Anchor | None:
    """The anchor a review comment can use, or None to report the finding in the summary.

    GitHub needs every anchored line inside the diff, and both ends of a multi-line comment in
    one hunk. If the range doesn't fit, try its first line, then its last. With no diff, only
    the finding's own `anchor_in_diff` flag is available.
    """
    anchor = finding.anchor
    if diff is None:
        return anchor if finding.anchor_in_diff else None
    file = diff.file(anchor.path)
    if file is None:
        return None
    for start, end in (
        (anchor.start_line, anchor.end_line),
        (anchor.start_line, anchor.start_line),
        (anchor.end_line, anchor.end_line),
    ):
        if file.is_commentable(anchor.side, start, end):
            return anchor.model_copy(update={"start_line": start, "end_line": end})
    return None


def evidence_label(item: Evidence) -> str:
    assert item.path is not None and item.start_line is not None and item.end_line is not None
    lines = (
        str(item.start_line)
        if item.end_line == item.start_line
        else f"{item.start_line}-{item.end_line}"
    )
    return f"{item.path}:{lines}"


def evidence_url(item: Evidence, target: ReviewTarget) -> str | None:
    """A permalink to the evidence at the analyzed commit, or None without a pull request."""
    prefix = permalink_prefix(target)
    if prefix is None:
        return None
    assert item.path is not None and item.start_line is not None and item.end_line is not None
    sha = target.merge_base_sha if item.ref == "base" else target.head_sha
    fragment = f"#L{item.start_line}"
    if item.end_line != item.start_line:
        fragment += f"-L{item.end_line}"
    return f"{prefix}{sha}/{quote(item.path)}{fragment}"


def location(item: Evidence, target: ReviewTarget) -> str:
    label = evidence_label(item)
    url = evidence_url(item, target)
    if url is None:
        return f"`{label}` at `{target.head_sha[:12]}`"
    return f"[`{label}`]({url})"


@dataclass
class Comment:
    finding_id: str
    anchor: Anchor
    body: str
    redactions: int = 0


@dataclass
class RenderedReview:
    comments: list[Comment] = field(default_factory=list)
    summary: str = ""  # the review body: summary, plus findings that can't be inline comments
    not_posted: list[str] = field(default_factory=list)  # finding ids kept out by the budget


def render_comment(
    finding: Finding, anchor: Anchor, target: ReviewTarget, note: str = ""
) -> Comment:
    """One finding as a comment, from structured fields only."""

    def safe(text: str) -> str:
        return sanitize(text, target)

    lines = [
        f"**{finding.severity.value.capitalize()} · {finding.category.value}:** "
        f"{safe(finding.title)}",
        "",
        safe(finding.explanation),
    ]
    evidence = [
        e
        for e in finding.evidence
        if e.role is not EvidenceRole.ABSENCE and e.path is not None and e.start_line is not None
    ]
    if evidence:
        lines += ["", "**Evidence**"]
        lines += [f"- {location(e, target)}: {safe(e.note)}" for e in evidence[:6]]
    if finding.suggested_fix:
        lines += ["", f"**Suggested fix:** {safe(finding.suggested_fix)}"]
    if finding.verification == "upheld":
        lines += ["", "An independent check tried to refute this and could not."]
    if note:
        lines += ["", note]
    lines += ["", FOOTER]
    body, redactions = scan_secrets("\n".join(lines))
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS].rstrip() + "\n\n[cut]"
    return Comment(finding.id, anchor, body, redactions)


def render_review(
    review: AgentReview, diff: ParsedDiff | None = None, *, posted_only: bool = True
) -> RenderedReview:
    """The inline comments, and a summary that carries every finding that can't be one.

    `posted_only` follows the noise budget (the review's `posted_ids`). Findings outside it, and
    findings with no usable anchor, are listed in the summary instead of becoming comments.
    """
    target = review.target
    by_id = {f.id: f for f in review.findings}
    rendered = RenderedReview()
    summary = [sanitize(review.summary, target)]
    for finding_id in review.posted_ids if posted_only else list(by_id):
        finding = by_id[finding_id]
        anchor = resolve_anchor(finding, diff)
        if anchor is None:
            summary.append(f"- {sanitize(finding.title, target)} (not inside the diff)")
            continue
        rendered.comments.append(render_comment(finding, anchor, target))
    posted = {c.finding_id for c in rendered.comments}
    rendered.not_posted = [f.id for f in review.findings if f.id not in posted]
    if rendered.not_posted:
        summary += ["", "Also noticed, not posted as comments:"]
        summary += [
            f"- {sanitize(by_id[i].title, target)} ({by_id[i].severity.value}, "
            f"{by_id[i].verification or 'unchecked'})"
            for i in rendered.not_posted
        ]
    text, _ = scan_secrets("\n".join(summary))
    rendered.summary = text.strip()
    return rendered
