"""The hosted web app: webhooks, GitHub sign-in, per-repository access, and posting as the App.

GitHub is a mock transport. Nothing here reaches the network.
"""

import hashlib
import hmac
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from pr_investigator.config import Settings
from pr_investigator.review.decisions import DecisionStore
from pr_investigator.review.report import write_run
from pr_investigator.service import store as states
from pr_investigator.service.access import AccessChecker
from pr_investigator.service.app_auth import AppAuth
from pr_investigator.service.oauth import OAuthClient
from pr_investigator.service.runtime import ServiceRuntime
from pr_investigator.service.store import ServiceStore
from pr_investigator.web.app import create_app
from tests.support_reviews import HEAD, make_review
from tests.support_service import PRIVATE_PEM, SECRET, pr_event, service_settings
from tests.unit.test_publish import PR_PATH, _pr

RUN_ID = "20261022-100000-abc123"
URL = "https://pri.example.com"
PERMISSIONS = {"alice": "write", "bob": "read", "dana": "admin"}  # carol isn't a collaborator


class FakeGitHub:
    def __init__(self) -> None:
        self.token_requests: list[dict[str, object]] = []
        self.posts: list[dict[str, object]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, path = str(request.url), request.url.path
        if url.startswith("https://github.com/login/oauth/access_token"):
            code = parse_qs(request.content.decode())["code"][0]
            return httpx.Response(200, json={"access_token": f"gho_{code}"})
        if url == "https://api.github.com/user":
            token = request.headers["authorization"].removeprefix("Bearer gho_")
            return httpx.Response(200, json={"login": token})
        if path.endswith("/access_tokens"):
            body = json.loads(request.content)
            self.token_requests.append(body)
            return httpx.Response(
                201, json={"token": "ghs_x", "expires_at": "x", "permissions": body["permissions"]}
            )
        if "/collaborators/" in path:
            login = path.split("/")[-2]
            if login not in PERMISSIONS:
                return httpx.Response(404)
            return httpx.Response(200, json={"permission": PERMISSIONS[login]})
        if path == PR_PATH:
            return httpx.Response(200, json=_pr(HEAD))
        if path == f"{PR_PATH}/reviews" and request.method == "GET":
            return httpx.Response(200, json=[])
        if path == f"{PR_PATH}/reviews" and request.method == "POST":
            self.posts.append(json.loads(request.content))
            return httpx.Response(200, json={"id": 77, "html_url": "https://github.com/r/77"})
        return httpx.Response(404)


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def runtime(github: FakeGitHub, tmp_path: Path) -> ServiceRuntime:
    transport = httpx.MockTransport(github.handler)
    auth = AppAuth("12345", PRIVATE_PEM, transport=transport)
    return ServiceRuntime(
        settings=service_settings(),
        store=ServiceStore(tmp_path / "service.sqlite"),
        auth=auth,
        oauth=OAuthClient("client-id", "client-secret", URL, transport=transport),
        access=AccessChecker(auth, transport=transport),
        github_transport=transport,
    )


@pytest.fixture
def served(settings: Settings, runtime: ServiceRuntime) -> str:
    """A run made by the service for acme/app#7, awaiting review."""
    write_run(make_review(), settings.runs_dir, RUN_ID)
    job, _ = runtime.store.enqueue("acme/app", 7, HEAD, 99, "d1")
    runtime.store.claim(states.QUEUED, states.FETCHING)
    runtime.store.advance(job.id, states.FETCHING, states.READY)
    runtime.store.claim(states.READY, states.ANALYZING)
    runtime.store.advance(job.id, states.ANALYZING, states.AWAITING_REVIEW, run_id=RUN_ID)
    return RUN_ID


def client(settings: Settings, runtime: ServiceRuntime) -> TestClient:
    return TestClient(create_app(settings, runtime=runtime), base_url=URL, follow_redirects=False)


def sign_in(c: TestClient, login: str) -> str:
    start = c.get("/login/github")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    done = c.get(f"/oauth/callback?code={login}&state={state}")
    assert done.status_code == 303 and done.headers["location"] == "/"
    match = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/").text)
    assert match
    return match.group(1)


def webhook(
    c: TestClient, body: bytes, *, signature: str | None = None, delivery: str = "d1"
) -> httpx.Response:
    sig = signature or "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return c.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-hub-signature-256": sig,
            "x-github-event": "pull_request",
            "x-github-delivery": delivery,
        },
    )


# --- webhook endpoint -----------------------------------------------------------------------


def test_the_webhook_checks_the_signature_before_looking_at_the_body(
    settings: Settings, runtime: ServiceRuntime
) -> None:
    c = client(settings, runtime)
    forged = webhook(c, b"{ not json", signature="sha256=" + "0" * 64)
    assert forged.status_code == 401  # rejected on the signature, before any parsing
    assert c.post("/webhooks/github", content=b"{}").status_code == 401  # no signature at all
    assert runtime.store.jobs_today(99) == 0


def test_a_signed_pull_request_event_is_acknowledged_fast_and_queued(
    settings: Settings, runtime: ServiceRuntime
) -> None:
    c = client(settings, runtime)
    response = webhook(c, pr_event())
    assert response.status_code == 202 and response.json()["status"] == "queued"
    assert runtime.store.jobs_today(99) == 1
    assert webhook(c, pr_event(), delivery="d1").json()["status"] == "duplicate"  # redelivery
    assert runtime.store.jobs_today(99) == 1
    body = pr_event()
    missing = c.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-hub-signature-256": "sha256="
            + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        },
    )
    assert missing.status_code == 400  # no event headers


def test_the_health_check_needs_no_sign_in_and_shows_nothing(
    settings: Settings, runtime: ServiceRuntime
) -> None:
    response = client(settings, runtime).get("/healthz")
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert "strict-transport-security" in response.headers


# --- sign-in --------------------------------------------------------------------------------


def test_the_hosted_app_signs_in_with_github_only(
    settings: Settings, runtime: ServiceRuntime
) -> None:
    c = client(settings, runtime)
    assert c.post("/login", data={"token": "anything"}).status_code == 404
    assert "Sign in with GitHub" in c.get("/login").text
    start = c.get("/login/github")
    location = urlparse(start.headers["location"])
    query = parse_qs(location.query)
    assert location.netloc == "github.com" and query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == [f"{URL}/oauth/callback"]
    cookie = start.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie


def test_a_callback_without_the_state_we_issued_does_not_sign_anyone_in(
    settings: Settings, runtime: ServiceRuntime
) -> None:
    c = client(settings, runtime)
    assert "error" in c.get("/oauth/callback?code=alice&state=x").headers["location"]  # no cookie
    c.get("/login/github")
    wrong = c.get("/oauth/callback?code=alice&state=not-the-state")
    assert "error" in wrong.headers["location"]
    assert c.get("/").status_code == 303


def test_the_session_cookie_is_secure_and_httponly(
    settings: Settings, runtime: ServiceRuntime
) -> None:
    c = client(settings, runtime)
    start = c.get("/login/github")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    cookie = c.get(f"/oauth/callback?code=alice&state={state}").headers["set-cookie"].lower()
    assert "pri_session=" in cookie and "httponly" in cookie and "secure" in cookie


# --- access control -------------------------------------------------------------------------


def test_people_with_write_access_see_the_run_and_others_do_not(
    settings: Settings, runtime: ServiceRuntime, served: str
) -> None:
    for login, allowed in (("alice", True), ("dana", True), ("bob", False), ("carol", False)):
        c = client(settings, runtime)
        sign_in(c, login)
        assert (served in c.get("/").text) is allowed, login
        assert c.get(f"/runs/{served}").status_code == (200 if allowed else 403), login
        assert c.get(f"/runs/{served}/trace").status_code == (200 if allowed else 403), login
        assert c.get(f"/runs/{served}/publish").status_code == (200 if allowed else 403), login


def test_a_read_only_user_cannot_decide_or_post(
    settings: Settings, runtime: ServiceRuntime, served: str, github: FakeGitHub
) -> None:
    c = client(settings, runtime)
    csrf = sign_in(c, "bob")
    assert (
        c.post(f"/runs/{served}/findings/F1", data={"action": "approve", "csrf": csrf}).status_code
        == 403
    )
    assert c.post(f"/runs/{served}/publish", data={"csrf": csrf}).status_code == 403
    assert DecisionStore(settings.review_db).decisions(served) == {}
    assert github.posts == []


def test_a_run_the_service_did_not_make_is_not_shown_to_anyone(
    settings: Settings, runtime: ServiceRuntime
) -> None:
    write_run(make_review(), settings.runs_dir, "20261001-000000-local1")  # a local CLI run
    c = client(settings, runtime)
    sign_in(c, "alice")
    assert "local1" not in c.get("/").text
    assert c.get("/runs/20261001-000000-local1").status_code == 403


def test_a_superseded_run_is_marked_in_the_list(
    settings: Settings, runtime: ServiceRuntime, served: str
) -> None:
    runtime.store.enqueue("acme/app", 7, "d" * 40, 99, "d2")  # a newer push
    runtime.store.advance(1, states.AWAITING_REVIEW, states.SUPERSEDED)
    c = client(settings, runtime)
    sign_in(c, "alice")
    assert "superseded by a newer push" in c.get("/").text


# --- publishing as the App ------------------------------------------------------------------


def test_publishing_posts_as_the_app_with_a_narrow_token_and_names_the_reviewer(
    settings: Settings, runtime: ServiceRuntime, served: str, github: FakeGitHub
) -> None:
    c = client(settings, runtime)
    csrf = sign_in(c, "alice")
    c.post(f"/runs/{served}/findings/F1", data={"action": "approve", "csrf": csrf})
    preview = c.get(f"/runs/{served}/publish")
    assert "Post this review to GitHub" in preview.text and "Approved by" in preview.text
    assert github.posts == []
    done = c.post(f"/runs/{served}/publish", data={"csrf": csrf})
    assert done.status_code == 303
    (review,) = github.posts
    assert review["event"] == "COMMENT" and review["commit_id"] == HEAD
    assert "Approved by `@alice`." in json.dumps(review["comments"])
    publisher_requests = [
        r for r in github.token_requests if r["permissions"] == {"pull_requests": "write"}
    ]
    assert publisher_requests and publisher_requests[-1]["repositories"] == ["app"]
    (publication,) = DecisionStore(settings.review_db).publications(served)
    assert publication.posted_by == "alice"
    assert DecisionStore(settings.review_db).decisions(served)["F1"].reviewer == "alice"
