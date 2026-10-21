"""Decisions and the publisher, against a mocked GitHub. Nothing here touches the network."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from pr_investigator.domain import Anchor, Severity, Side
from pr_investigator.errors import PublishError
from pr_investigator.github import GitHubClient
from pr_investigator.publish import build_review, collapse_comments, publish
from pr_investigator.review.decisions import (
    Decision,
    DecisionStore,
    FindingEdit,
    ReasonCode,
)
from tests.support_reviews import HEAD, finding, make_review

PR_PATH = "/repos/acme/app/pulls/7"


class FakeGitHub:
    """Records requests; answers like GitHub unless told otherwise."""

    def __init__(self, *, head: str = HEAD, reviews: list[dict[str, Any]] | None = None) -> None:
        self.head = head
        self.reviews = reviews or []
        self.posts: list[dict[str, Any]] = []
        self.reject_inline = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/user":
            return httpx.Response(200, json={"login": "octocat"})
        if request.method == "GET" and path == PR_PATH:
            return httpx.Response(200, json=_pr(self.head))
        if request.method == "GET" and path == f"{PR_PATH}/reviews":
            return httpx.Response(200, json=self.reviews)
        if request.method == "POST" and path == f"{PR_PATH}/reviews":
            body = json.loads(request.content)
            self.posts.append(body)
            if self.reject_inline and body["comments"]:
                return httpx.Response(422, json={"message": "Line could not be resolved"})
            return httpx.Response(200, json={"id": 99, "html_url": "https://github.com/r/99"})
        return httpx.Response(404)

    def client(self) -> GitHubClient:
        return GitHubClient("t", transport=httpx.MockTransport(self.handler))


def _pr(head: str) -> dict[str, Any]:
    return {
        "title": "t",
        "body": "",
        "user": {"login": "someone"},
        "state": "open",
        "html_url": "https://github.com/acme/app/pull/7",
        "base": {
            "ref": "main",
            "sha": "b" * 40,
            "repo": {"private": False, "clone_url": "https://github.com/acme/app.git"},
        },
        "head": {"ref": "feature", "sha": head},
    }


@pytest.fixture
def store(tmp_path: Path) -> DecisionStore:
    return DecisionStore(tmp_path / "review.sqlite")


def approve(store: DecisionStore, *ids: str, edit: FindingEdit | None = None) -> None:
    for finding_id in ids:
        store.decide("run1", finding_id, Decision.APPROVED, reviewer="me", edit=edit)


def test_rejecting_needs_a_reason_and_decisions_are_replaceable_but_kept_in_history(
    store: DecisionStore,
) -> None:
    with pytest.raises(PublishError, match="reason code"):
        store.decide("run1", "F1", Decision.REJECTED, reviewer="me")
    store.decide("run1", "F1", Decision.REJECTED, reviewer="me", reason_code=ReasonCode.DUPLICATE)
    store.decide("run1", "F1", Decision.APPROVED, reviewer="me", reason_code=ReasonCode.OTHER)
    (kept,) = store.decisions("run1").values()
    assert kept.decision is Decision.APPROVED and kept.reason_code is None
    history = store._db.execute("SELECT decision FROM decision_history ORDER BY rowid").fetchall()
    assert [h[0] for h in history] == ["rejected", "approved"]  # labelled feedback keeps both
    store.clear("run1", "F1")
    assert store.decisions("run1") == {}


def test_the_payload_has_only_approved_findings_with_edits_and_exact_anchors(
    store: DecisionStore,
) -> None:
    multi = finding("F3", anchor=Anchor(path="app/b.py", side=Side.LEFT, start_line=9, end_line=9))
    review = make_review(finding("F1"), finding("F2"), multi)
    approve(store, "F1", edit=FindingEdit(title="Better title", severity=Severity.CRITICAL))
    approve(store, "F3")
    store.decide("run1", "F2", Decision.REJECTED, reviewer="me", reason_code=ReasonCode.UNCLEAR)

    built = build_review(review, store.decisions("run1"), run_id="run1", approved_by="octocat")
    payload = built.payload
    assert payload["event"] == "COMMENT" and payload["commit_id"] == HEAD
    first, second = payload["comments"]
    assert first["path"] == "app/a.py" and first["line"] == 4 and first["side"] == "RIGHT"
    assert first["start_line"] == 3 and first["start_side"] == "RIGHT"  # a multi-line range
    assert "Better title" in first["body"] and "Critical" in first["body"]
    assert second["path"] == "app/b.py" and second["side"] == "LEFT" and "start_line" not in second
    assert "Second" not in json.dumps(payload) and "Problem F2" not in json.dumps(payload)
    assert "Approved by `@octocat`." in first["body"]
    assert f"<!-- pri-publication:{built.key} -->" in payload["body"]
    assert "Not reviewed:" in payload["body"] and "big.lock: lockfile" in payload["body"]
    assert "<img" not in payload["body"]  # the model's summary is sanitized


def test_nothing_approved_means_nothing_to_publish(store: DecisionStore) -> None:
    with pytest.raises(PublishError, match="no finding is approved"):
        build_review(make_review(), store.decisions("run1"), run_id="run1")


def test_the_key_is_stable_and_changes_with_the_approved_content(store: DecisionStore) -> None:
    review = make_review()
    approve(store, "F1")
    a = build_review(review, store.decisions("run1"), run_id="run1").key
    b = build_review(review, store.decisions("run1"), run_id="run1").key
    approve(store, "F1", edit=FindingEdit(title="changed"))
    c = build_review(review, store.decisions("run1"), run_id="run1").key
    assert a == b != c


def test_a_finding_outside_the_diff_goes_into_the_summary(store: DecisionStore) -> None:
    outside = finding("F1", anchor_in_diff=False)
    approve(store, "F1")
    built = build_review(make_review(outside), store.decisions("run1"), run_id="run1")
    assert built.payload["comments"] == [] and built.summary_only == ["F1"]
    assert "Also noticed, outside the changed lines" in built.payload["body"]


def test_a_preview_sends_nothing(store: DecisionStore) -> None:
    fake = FakeGitHub()
    approve(store, "F1")
    result = publish(fake.client(), make_review(), store, run_id="run1", post=False)
    assert result.status == "preview" and fake.posts == []
    assert store.publications("run1") == []


def test_posting_makes_one_call_and_records_it(store: DecisionStore) -> None:
    fake = FakeGitHub()
    approve(store, "F1", "F2")
    result = publish(fake.client(), make_review(), store, run_id="run1", post=True)
    assert result.status == "posted" and result.review_id == 99 and not result.fell_back
    (body,) = fake.posts
    assert len(body["comments"]) == 2 and body["event"] == "COMMENT"
    (publication,) = store.publications("run1")
    assert publication.github_review_id == 99 and publication.posted_by == "octocat"
    assert publication.comments == 2


def test_a_retry_finds_its_marker_and_posts_nothing(store: DecisionStore) -> None:
    review = make_review()
    approve(store, "F1")
    key = build_review(review, store.decisions("run1"), run_id="run1").key
    fake = FakeGitHub(
        reviews=[{"id": 5, "html_url": "u", "body": f"x <!-- pri-publication:{key} -->"}]
    )
    result = publish(fake.client(), review, store, run_id="run1", post=True)
    assert result.status == "already_published" and result.review_id == 5
    assert fake.posts == []


def test_a_stale_head_is_refused_unless_allowed(store: DecisionStore) -> None:
    fake = FakeGitHub(head="c" * 40)
    approve(store, "F1")
    with pytest.raises(PublishError, match="new commits"):
        publish(fake.client(), make_review(), store, run_id="run1", post=True)
    assert fake.posts == []
    result = publish(
        fake.client(), make_review(), store, run_id="run1", post=True, allow_stale=True
    )
    assert result.status == "posted" and fake.posts[0]["commit_id"] == HEAD  # the analyzed commit


def test_a_422_moves_the_comments_into_the_summary_and_still_succeeds(
    store: DecisionStore,
) -> None:
    fake = FakeGitHub()
    fake.reject_inline = True
    approve(store, "F1", "F2")
    result = publish(fake.client(), make_review(), store, run_id="run1", post=True)
    assert result.status == "posted" and result.fell_back
    assert len(fake.posts) == 2 and fake.posts[1]["comments"] == []
    body = fake.posts[1]["body"]
    assert "GitHub could not place these comments" in body and "Problem F1" in body
    assert body.rstrip().endswith("-->")  # the marker is still last
    assert store.publications("run1")[0].comments == 0


def test_collapsing_keeps_the_marker_and_every_comment(store: DecisionStore) -> None:
    approve(store, "F1", "F2")
    built = build_review(make_review(), store.decisions("run1"), run_id="run1")
    flat = collapse_comments(built)
    assert flat.payload["comments"] == [] and flat.summary_only == ["F1", "F2"]
    assert flat.payload["body"].count("pri-publication") == 1
    assert "app/a.py" in flat.payload["body"] and "line 3-4" in flat.payload["body"]


def test_a_local_run_has_nowhere_to_post(store: DecisionStore) -> None:
    review = make_review()
    local = review.model_copy(
        update={"target": review.target.model_copy(update={"pull_request": None})}
    )
    approve(store, "F1")
    with pytest.raises(PublishError, match="isn't a GitHub pull request"):
        publish(FakeGitHub().client(), local, store, run_id="run1", post=False)
