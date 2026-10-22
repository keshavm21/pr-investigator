"""The review UI through its test client: access, escaping, decisions, preview and publishing."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pr_investigator.config import Settings
from pr_investigator.github import GitHubClient
from pr_investigator.review.decisions import Decision, DecisionStore
from pr_investigator.review.report import write_run
from pr_investigator.web.app import create_app
from tests.support_reviews import finding, make_review
from tests.unit.test_publish import FakeGitHub

TOKEN = "test-token"


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def run_id(settings: Settings) -> str:
    evil = finding(
        "F2", title="<script>alert('x')</script> & more", explanation="</pre><b>bold</b>"
    )
    review = make_review(finding("F1"), evil)
    return write_run(review, settings.runs_dir, "20261021-100000-abc").name


def client_for(settings: Settings, fake: FakeGitHub | None = None) -> TestClient:
    def github() -> GitHubClient | None:
        return fake.client() if fake else None

    return TestClient(create_app(settings, token=TOKEN, github=github), follow_redirects=False)


def login(client: TestClient) -> str:
    response = client.post("/login", data={"token": TOKEN})
    assert response.status_code == 303
    page = client.get("/")
    match = re.search(r'name="csrf" value="([0-9a-f]+)"', page.text)
    assert match, page.text
    return match.group(1)


def test_every_page_needs_the_token(settings: Settings, run_id: str) -> None:
    client = client_for(settings)
    for path in ("/", f"/runs/{run_id}", f"/runs/{run_id}/trace", f"/runs/{run_id}/publish"):
        response = client.get(path)
        assert response.status_code == 303 and response.headers["location"] == "/login"
    assert "Wrong token" in client.post("/login", data={"token": "nope"}).text
    assert client.get("/").status_code == 303  # a failed login doesn't sign in


def test_security_headers_and_a_strict_csp(settings: Settings) -> None:
    response = client_for(settings).get("/login")
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script" not in csp and "unsafe" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert (
        "samesite=strict"
        in client_for(settings).post("/login", data={"token": TOKEN}).headers["set-cookie"].lower()
    )


def test_runs_list_and_run_page_escape_everything(settings: Settings, run_id: str) -> None:
    client = client_for(settings)
    login(client)
    assert run_id in client.get("/").text
    page = client.get(f"/runs/{run_id}").text
    assert "<script>alert" not in page and "&lt;script&gt;alert" in page
    assert "<b>bold</b>" not in page and "<img src=x>" not in page
    assert "Add &lt;b&gt;search&lt;/b&gt;" in page
    assert "https://github.com/acme/app/blob/" in page  # evidence permalink
    assert "inside the noise budget" in page


def test_run_ids_cannot_escape_the_runs_directory(settings: Settings, run_id: str) -> None:
    client = client_for(settings)
    login(client)
    for bad in ("%2e%2e", "..%2f..%2fetc", "a b", "x" * 100):
        assert client.get(f"/runs/{bad}").status_code in (404, 422)
    assert client.get("/runs/does-not-exist").status_code == 404
    assert client.get(f"/runs/{run_id}/trace").status_code == 200


def test_state_changes_need_a_csrf_token(settings: Settings, run_id: str) -> None:
    client = client_for(settings)
    csrf = login(client)
    url = f"/runs/{run_id}/findings/F1"
    assert client.post(url, data={"action": "approve", "csrf": "bad"}).status_code == 403
    assert client.post(url, data={"action": "approve"}).status_code == 422
    assert client.post("/logout", data={"csrf": "bad"}).status_code == 403
    store = DecisionStore(settings.review_db)
    assert store.decisions(run_id) == {}
    ok = client.post(url, data={"action": "approve", "csrf": csrf})
    assert ok.status_code == 303
    assert store.decisions(run_id)["F1"].decision is Decision.APPROVED


def test_approving_with_edits_and_rejecting_with_a_reason_are_stored(
    settings: Settings, run_id: str
) -> None:
    client = client_for(settings)
    csrf = login(client)
    form = {
        "csrf": csrf,
        "title": "A better title",
        "explanation": "Explanation F1.",
        "severity": "critical",
        "suggested_fix": "",
        "reason_code": "duplicate",
    }
    client.post(f"/runs/{run_id}/findings/F1", data={**form, "action": "approve"})
    client.post(
        f"/runs/{run_id}/findings/F2",
        data={
            **form,
            "title": "<script>alert('x')</script> & more",
            "action": "reject",
            "reason_text": "same as F1",
        },
    )
    decisions = DecisionStore(settings.review_db).decisions(run_id)
    approved, rejected = decisions["F1"], decisions["F2"]
    assert approved.edit.title == "A better title" and approved.edit.severity == "critical"
    assert approved.edit.explanation is None  # unchanged fields are not edits
    assert rejected.decision is Decision.REJECTED and rejected.reason_code == "duplicate"
    assert rejected.reason_text == "same as F1"
    page = client.get(f"/runs/{run_id}").text
    assert "approved" in page and "rejected: duplicate" in page
    bad = client.post(
        f"/runs/{run_id}/findings/F1", data={**form, "action": "approve", "severity": "bogus"}
    )
    assert bad.status_code == 400
    assert (
        client.post(f"/runs/{run_id}/findings/F9", data={**form, "action": "approve"}).status_code
        == 404
    )


def test_the_preview_shows_the_exact_payload_and_posting_needs_confirmation(
    settings: Settings, run_id: str, fake: FakeGitHub
) -> None:
    client = client_for(settings, fake)
    csrf = login(client)
    empty = client.get(f"/runs/{run_id}/publish").text
    assert "no finding is approved" in empty and "Post this review" not in empty
    client.post(f"/runs/{run_id}/findings/F1", data={"action": "approve", "csrf": csrf})
    preview = client.get(f"/runs/{run_id}/publish")
    assert preview.status_code == 200 and "Post this review to GitHub" in preview.text
    assert "&quot;event&quot;: &quot;COMMENT&quot;" in preview.text.replace("&#34;", "&quot;")
    assert fake.posts == []  # viewing the preview posts nothing
    assert client.post(f"/runs/{run_id}/publish", data={"csrf": "bad"}).status_code == 403
    assert fake.posts == []
    done = client.post(f"/runs/{run_id}/publish", data={"csrf": csrf})
    assert done.status_code == 303 and len(fake.posts) == 1
    assert json.dumps(fake.posts[0]["comments"]).count("Problem F1") == 1
    assert "Published" in client.get(f"/runs/{run_id}").text


def test_without_a_github_token_the_preview_cannot_post(settings: Settings, run_id: str) -> None:
    client = client_for(settings)
    csrf = login(client)
    client.post(f"/runs/{run_id}/findings/F1", data={"action": "approve", "csrf": csrf})
    page = client.get(f"/runs/{run_id}/publish").text
    assert "Set GITHUB_TOKEN" in page and "Post this review" not in page
    assert client.post(f"/runs/{run_id}/publish", data={"csrf": csrf}).status_code == 400


def test_a_stale_head_is_shown_and_blocks_posting_until_allowed(
    settings: Settings, run_id: str
) -> None:
    fake = FakeGitHub(head="c" * 40)
    client = client_for(settings, fake)
    csrf = login(client)
    client.post(f"/runs/{run_id}/findings/F1", data={"action": "approve", "csrf": csrf})
    assert "new commits" in client.get(f"/runs/{run_id}").text
    blocked = client.post(f"/runs/{run_id}/publish", data={"csrf": csrf})
    assert blocked.status_code == 200 and "new commits" in blocked.text and fake.posts == []
    allowed = client.post(f"/runs/{run_id}/publish", data={"csrf": csrf, "allow_stale": "1"})
    assert allowed.status_code == 303 and len(fake.posts) == 1


def test_sign_out_ends_the_session(settings: Settings, run_id: str) -> None:
    client = client_for(settings)
    csrf = login(client)
    assert client.post("/logout", data={"csrf": csrf}).status_code == 303
    assert client.get("/").status_code == 303


def test_a_corrupt_run_file_does_not_break_the_list(settings: Settings, run_id: str) -> None:
    bad = settings.runs_dir / "20261021-100001-bad"
    bad.mkdir(parents=True)
    (bad / "run.json").write_text("{not json", encoding="utf-8")
    client = client_for(settings)
    login(client)
    assert client.get("/").status_code == 200
    assert Path(settings.runs_dir / run_id / "run.json").is_file()


def test_the_preview_filter_renders_a_few_constructs_and_nothing_else() -> None:
    from pr_investigator.web.app import md_preview

    link = "[`a.py:3`](https://github.com/acme/app/blob/" + "a" * 40 + "/a.py#L3)"
    out = str(md_preview(f"**Bold** `code` {link} <sub>small</sub>"))
    assert "<strong>Bold</strong>" in out and "<code>code</code>" in out
    assert 'href="https://github.com/acme/app/blob/' in out and "<span class=" in out
    # Nothing in the text can become markup, and only github.com links are made clickable.
    hostile = md_preview(
        "<script>alert(1)</script> <img src=x onerror=1> [x](https://evil.example/a) "
        "[y](javascript:alert(1)) **<b>**"
    )
    text = str(hostile)
    assert "<script" not in text and "<img" not in text and "<b>" not in text
    assert "href" not in text and "&lt;script&gt;" in text
    marker = str(md_preview("<!-- pri-publication:2fe7e05c33bb4985 -->"))
    assert "idempotency marker 2fe7e05c33bb4985" in marker and "<!--" not in marker
