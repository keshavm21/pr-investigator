"""The publisher: approved findings become one GitHub review. The only code that posts.

Rules (docs/architecture.md §4.12, invariant 4):
- only findings a human approved, with their edits, rendered through the fixed template and
  the sanitizer (review/comments.py);
- one review per publication, `event: COMMENT`, `commit_id` the analyzed head, never APPROVE or
  REQUEST_CHANGES;
- idempotent: the review body carries a hidden marker, and a retry first looks for it;
- stale head: refuses when the PR has moved on, unless told otherwise;
- a 422 from GitHub (an anchor it can't resolve) puts every comment in the review body instead,
  so nothing is lost and the publication still succeeds.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pr_investigator.domain import Anchor, Finding
from pr_investigator.errors import GitHubValidationError, PublishError
from pr_investigator.github import GitHubClient
from pr_investigator.review.comments import (
    FOOTER,
    render_comment,
    resolve_anchor,
    sanitize,
    scan_secrets,
)
from pr_investigator.review.decisions import (
    Decision,
    DecisionStore,
    Publication,
    ReviewDecision,
    apply_edit,
)
from pr_investigator.review.reviewers import AnyReview

MARKER = "pri-publication"


@dataclass
class BuiltReview:
    payload: dict[str, Any]
    key: str
    inline: list[str] = field(default_factory=list)  # finding ids posted as line comments
    summary_only: list[str] = field(default_factory=list)  # finding ids in the review body


@dataclass
class PublishResult:
    status: str  # "preview", "posted" or "already_published"
    built: BuiltReview
    review_id: int | None = None
    url: str | None = None
    fell_back: bool = False  # GitHub rejected an anchor, so the comments went into the body


def approved_findings(review: AnyReview, decisions: dict[str, ReviewDecision]) -> list[Finding]:
    """The approved findings in rank order, with the reviewer's edits applied."""
    out = []
    for finding in review.findings:
        decision = decisions.get(finding.id)
        if decision is not None and decision.decision is Decision.APPROVED:
            out.append(apply_edit(finding, decision.edit))
    return out


def check_stale(client: GitHubClient, review: AnyReview) -> str | None:
    """A warning when the pull request has new commits since the analysis, else None."""
    pr = review.target.pull_request
    if pr is None:
        return None
    head = client.get_pull_request(pr).head_sha
    if head == review.target.head_sha:
        return None
    return (
        f"the pull request has new commits (analyzed {review.target.head_sha[:12]}, now "
        f"{head[:12]}); comments would be placed against the analyzed commit"
    )


def publication_key(run_id: str, head_sha: str, findings: list[Finding]) -> str:
    """Stable for the same run, commit and approved content, so a retry finds its marker."""
    parts = [run_id, head_sha]
    for f in sorted(findings, key=lambda f: f.id):
        parts += [f.id, f.title, f.explanation, f.severity.value, f.suggested_fix or ""]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


def build_review(
    review: AnyReview,
    decisions: dict[str, ReviewDecision],
    *,
    run_id: str,
    approved_by: str | None = None,
) -> BuiltReview:
    """The exact payload that would be sent. Raises if nothing is approved."""
    target = review.target
    findings = approved_findings(review, decisions)
    if not findings:
        raise PublishError("no finding is approved; approve at least one to publish")
    note = f"Approved by `@{approved_by}`." if approved_by else "Approved by a human reviewer."
    comments: list[dict[str, Any]] = []
    inline: list[str] = []
    summary_only: list[str] = []
    for finding in findings:
        anchor = resolve_anchor(finding, None)
        if anchor is None:
            summary_only.append(finding.id)
            continue
        text = render_comment(finding, anchor, target, note).body
        comments.append(_comment(anchor, text))
        inline.append(finding.id)
    key = publication_key(run_id, target.head_sha, findings)
    body = [sanitize(review.summary, target)]
    by_id = {f.id: f for f in findings}
    if summary_only:
        body += ["", "Also noticed, outside the changed lines:"]
        body += [
            f"- {sanitize(by_id[i].title, target)}: {sanitize(by_id[i].explanation, target)}"
            for i in summary_only
        ]
    if review.coverage.skipped:
        body += ["", "Not reviewed:"]
        body += [
            f"- {sanitize(path, target)}: {sanitize(reason, target)}"
            for path, reason in review.coverage.skipped.items()
        ]
    body += ["", FOOTER, f"<!-- {MARKER}:{key} -->"]
    text, _ = scan_secrets("\n".join(body))
    payload: dict[str, Any] = {
        "commit_id": target.head_sha,
        "event": "COMMENT",
        "body": text,
        "comments": comments,
    }
    return BuiltReview(payload, key, inline, summary_only)


def _comment(anchor: Anchor, body: str) -> dict[str, Any]:
    comment: dict[str, Any] = {
        "path": anchor.path,
        "body": body,
        "line": anchor.end_line,
        "side": anchor.side.value,
    }
    if anchor.end_line != anchor.start_line:
        comment["start_line"] = anchor.start_line
        comment["start_side"] = anchor.side.value
    return comment


def collapse_comments(built: BuiltReview) -> BuiltReview:
    """The same review with every line comment moved into the body (the 422 fallback)."""
    payload = dict(built.payload)
    marker_at = payload["body"].rindex(f"<!-- {MARKER}:")
    moved = [
        f"**`{c['path']}` line {c.get('start_line', c['line'])}"
        + (f"-{c['line']}" if "start_line" in c else "")
        + f"**\n\n{c['body']}"
        for c in payload["comments"]
    ]
    payload["body"] = (
        payload["body"][:marker_at]
        + "\n\n".join(["GitHub could not place these comments on lines:", *moved])
        + "\n\n"
        + payload["body"][marker_at:]
    )
    payload["comments"] = []
    return BuiltReview(payload, built.key, [], built.inline + built.summary_only)


def publish(
    client: GitHubClient,
    review: AnyReview,
    store: DecisionStore,
    *,
    run_id: str,
    post: bool,
    allow_stale: bool = False,
    approved_by: str | None = None,
) -> PublishResult:
    """Preview (post=False) or send the review for the approved findings.

    `approved_by` is the reviewer's GitHub login when the poster isn't the reviewer (the hosted
    service posts as the App). Without it the token's owner is the reviewer, as in local mode.
    """
    pr = review.target.pull_request
    if pr is None:
        raise PublishError("this run isn't a GitHub pull request, so there is nothing to post to")
    decisions = store.decisions(run_id)
    login = approved_by or client.get_login()
    built = build_review(review, decisions, run_id=run_id, approved_by=login)
    stale = check_stale(client, review)
    if stale is not None and not allow_stale:
        raise PublishError(f"{stale}. Re-run the review, or allow a stale publish.")
    if not post:
        return PublishResult("preview", built)
    marker = f"<!-- {MARKER}:{built.key} -->"
    for existing in client.list_reviews(pr):
        if marker in str(existing.get("body") or ""):
            return PublishResult(
                "already_published", built, existing.get("id"), existing.get("html_url")
            )
    fell_back = False
    try:
        data = client.create_review(pr, built.payload)
    except GitHubValidationError:
        built = collapse_comments(built)
        fell_back = True
        data = client.create_review(pr, built.payload)
    store.record_publication(
        Publication(
            run_id=run_id,
            key=built.key,
            github_review_id=data.get("id"),
            html_url=data.get("html_url"),
            commit_id=review.target.head_sha,
            posted_by=login,
            comments=len(built.payload["comments"]),
            summary_only=len(built.summary_only),
            published_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    )
    return PublishResult("posted", built, data.get("id"), data.get("html_url"), fell_back)
