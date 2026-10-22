"""Service building blocks: App auth, webhooks, admission, the job store and repo config."""

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest

from pr_investigator.errors import GitHubError
from pr_investigator.service import store as states
from pr_investigator.service.app_auth import AppAuth, app_jwt
from pr_investigator.service.repo_config import (
    CONFIG_PATH,
    RepoConfig,
    apply_ignore,
    load_repo_config,
)
from pr_investigator.service.store import ServiceStore
from pr_investigator.service.webhooks import handle, verify_signature
from tests.support import MakeRepo, OpenWorkspace
from tests.support_service import PRIVATE_PEM, PUBLIC_PEM, SECRET, pr_event, service_settings


@pytest.fixture
def store(tmp_path: Path) -> ServiceStore:
    return ServiceStore(tmp_path / "service.sqlite")


# --- GitHub App authentication --------------------------------------------------------------


def test_the_app_jwt_is_rs256_short_lived_and_names_the_app() -> None:
    token = app_jwt("12345", PRIVATE_PEM, now=1_000_000)
    claims = jwt.decode(token, PUBLIC_PEM, algorithms=["RS256"], options={"verify_exp": False})
    assert claims["iss"] == "12345"
    assert claims["iat"] == 1_000_000 - 60  # backdated against clock drift
    assert claims["exp"] - 1_000_000 <= 600  # GitHub's limit is 10 minutes from now
    assert jwt.get_unverified_header(token)["alg"] == "RS256"


def installation_transport(
    granted: dict[str, str] | None, seen: list[httpx.Request]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "token": "ghs_secret",
                "expires_at": "2026-10-22T12:00:00Z",
                "permissions": body["permissions"] if granted is None else granted,
            },
        )

    return httpx.MockTransport(handler)


def test_each_job_gets_a_token_for_one_repository_and_one_permission() -> None:
    seen: list[httpx.Request] = []
    auth = AppAuth("12345", PRIVATE_PEM, transport=installation_transport(None, seen))
    fetcher = auth.fetcher_token(99, "app")
    publisher = auth.publisher_token(99, "app")
    assert fetcher.token == publisher.token == "ghs_secret"
    bodies = [json.loads(r.content) for r in seen]
    assert bodies[0] == {"repositories": ["app"], "permissions": {"contents": "read"}}
    assert bodies[1] == {"repositories": ["app"], "permissions": {"pull_requests": "write"}}
    assert seen[0].url.path == "/app/installations/99/access_tokens"
    bearer = seen[0].headers["authorization"].removeprefix("Bearer ")
    assert (
        jwt.decode(bearer, PUBLIC_PEM, algorithms=["RS256"], options={"verify_exp": False})["iss"]
        == "12345"
    )


@pytest.mark.parametrize("granted", [{}, {"pull_requests": "read"}, {"contents": "write"}])
def test_a_token_that_grants_less_than_asked_is_refused(granted: dict[str, str]) -> None:
    auth = AppAuth("1", PRIVATE_PEM, transport=installation_transport(granted, []))
    with pytest.raises(GitHubError, match="doesn't grant pull_requests: write"):
        auth.publisher_token(99, "app")


def test_a_token_that_grants_more_than_the_floor_is_fine() -> None:
    auth = AppAuth("1", PRIVATE_PEM, transport=installation_transport({"contents": "write"}, []))
    assert auth.fetcher_token(99, "app").token == "ghs_secret"  # write includes read


# --- webhooks -------------------------------------------------------------------------------


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signatures_are_checked_against_the_raw_body() -> None:
    body = b'{"a": 1}'
    assert verify_signature(SECRET, body, sign(body))
    assert not verify_signature(SECRET, body + b" ", sign(body))  # the body changed
    assert not verify_signature(SECRET, body, sign(body, "other"))
    assert not verify_signature(SECRET, body, "sha256=")
    assert not verify_signature(SECRET, body, sign(body).removeprefix("sha256="))  # no prefix
    assert not verify_signature(SECRET, body, None)
    assert not verify_signature("", body, sign(body, ""))  # an unset secret never verifies


def deliver(
    store: ServiceStore,
    body: bytes,
    delivery: str = "d1",
    event: str = "pull_request",
    **settings: Any,
):  # type: ignore[no-untyped-def]
    return handle(event, delivery, body, service_settings(**settings), store)


def test_an_opened_pull_request_on_an_allowlisted_repository_is_queued(store: ServiceStore) -> None:
    outcome = deliver(store, pr_event("opened"))
    assert outcome.status == "queued" and outcome.job_id is not None
    job = store.get(outcome.job_id)
    assert job is not None and job.state == states.QUEUED
    assert (job.repo, job.pr_number, job.installation_id) == ("acme/app", 7, 99)


def test_a_redelivery_is_recognised_and_does_nothing(store: ServiceStore) -> None:
    assert deliver(store, pr_event(), "same").status == "queued"
    again = deliver(store, pr_event(), "same")
    assert again.status == "duplicate"
    assert store.jobs_today(99) == 1


def test_a_new_push_supersedes_the_old_job_but_not_other_pull_requests(store: ServiceStore) -> None:
    first = deliver(store, pr_event(head="1" * 40), "d1")
    other = deliver(store, pr_event(number=8), "d2")
    second = deliver(store, pr_event("synchronize", head="2" * 40), "d3")
    assert second.superseded == (first.job_id,)
    assert store.get(first.job_id or 0).state == states.SUPERSEDED  # type: ignore[union-attr]
    assert store.get(other.job_id or 0).state == states.QUEUED  # type: ignore[union-attr]
    assert store.get(second.job_id or 0).state == states.QUEUED  # type: ignore[union-attr]


def test_closing_a_pull_request_stops_its_jobs(store: ServiceStore) -> None:
    queued = deliver(store, pr_event(), "d1")
    closed = deliver(store, pr_event("closed", state="closed"), "d2")
    assert closed.superseded == (queued.job_id,)
    assert store.get(queued.job_id or 0).state == states.SUPERSEDED  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("body", "settings", "why"),
    [
        (pr_event(repo="evil/app"), {}, "isn't on the allowlist"),
        (pr_event(), {"allowed_repos": ""}, "isn't on the allowlist"),  # closed by default
        (pr_event(draft=True), {}, "drafts"),
        (pr_event(private=True), {}, "private repositories are disabled"),
        (pr_event(files=101), {}, "changed files"),
        (pr_event(lines=5_001), {}, "changed lines"),
        (pr_event(state="closed"), {}, "isn't open"),
    ],
)
def test_the_admission_policy_refuses_what_it_should(
    store: ServiceStore, body: bytes, settings: dict[str, Any], why: str
) -> None:
    outcome = deliver(store, body, **settings)
    assert outcome.status == "rejected" and why in outcome.reason
    assert store.jobs_today(99) == 0


def test_private_repositories_can_be_allowed_by_the_operator(store: ServiceStore) -> None:
    assert deliver(store, pr_event(private=True), allow_private=True).status == "queued"


def test_a_draft_is_analyzed_once_it_is_ready(store: ServiceStore) -> None:
    assert deliver(store, pr_event(draft=True), "d1").status == "rejected"
    assert deliver(store, pr_event("ready_for_review"), "d2").status == "queued"


def test_the_daily_budget_per_installation_is_enforced(store: ServiceStore) -> None:
    for n in range(2):
        assert (
            deliver(store, pr_event(number=n + 1), f"d{n}", daily_runs_per_installation=2).status
            == "queued"
        )
    over = deliver(store, pr_event(number=9), "d9", daily_runs_per_installation=2)
    assert over.status == "rejected" and "daily budget" in over.reason
    other = deliver(
        store, pr_event(number=9, installation=100), "d10", daily_runs_per_installation=2
    )
    assert other.status == "queued"  # budgets are per installation


def test_installation_lifecycle_events_gate_later_pull_requests(store: ServiceStore) -> None:
    created = json.dumps(
        {"action": "created", "installation": {"id": 99, "account": {"login": "acme"}}}
    ).encode()
    assert deliver(store, created, "i1", "installation").status == "recorded"
    assert store.installation_state(99) == "active"
    suspend = json.dumps(
        {"action": "suspend", "installation": {"id": 99, "account": {"login": "acme"}}}
    ).encode()
    deliver(store, suspend, "i2", "installation")
    refused = deliver(store, pr_event(), "d1")
    assert refused.status == "rejected" and "suspended" in refused.reason
    deliver(store, created.replace(b"created", b"unsuspend"), "i3", "installation")
    assert deliver(store, pr_event(), "d2").status == "queued"


def test_odd_deliveries_are_handled_without_error(store: ServiceStore) -> None:
    assert deliver(store, b"not json", "x1").status == "rejected"
    assert deliver(store, b"[1, 2]", "x2").status == "rejected"
    assert deliver(store, b"{}", "x3", "ping").status == "ignored"
    assert deliver(store, pr_event(), "x4", "star").status == "ignored"
    assert deliver(store, pr_event("edited"), "x5").status == "ignored"
    no_head = json.loads(pr_event())
    no_head["pull_request"]["head"] = {}
    assert deliver(store, json.dumps(no_head).encode(), "x6").status == "rejected"
    no_id = json.loads(pr_event())
    del no_id["installation"]
    assert deliver(store, json.dumps(no_id).encode(), "x7").status == "rejected"


# --- the job store --------------------------------------------------------------------------


def test_jobs_are_claimed_oldest_first_and_only_once(store: ServiceStore) -> None:
    a, _ = store.enqueue("acme/app", 1, "a" * 40, 99, "d1")
    b, _ = store.enqueue("acme/app", 2, "b" * 40, 99, "d2")
    first = store.claim(states.QUEUED, states.FETCHING)
    second = store.claim(states.QUEUED, states.FETCHING)
    assert (first.id, second.id) == (a.id, b.id)  # type: ignore[union-attr]
    assert store.claim(states.QUEUED, states.FETCHING) is None


def test_a_superseded_job_cannot_be_moved_forward_by_a_slow_worker(store: ServiceStore) -> None:
    old, _ = store.enqueue("acme/app", 1, "a" * 40, 99, "d1")
    store.claim(states.QUEUED, states.FETCHING)
    store.enqueue("acme/app", 1, "b" * 40, 99, "d2")  # a new push while it was fetching
    assert store.get(old.id).state == states.SUPERSEDED  # type: ignore[union-attr]
    assert not store.advance(old.id, states.FETCHING, states.READY)
    assert store.get(old.id).state == states.SUPERSEDED  # type: ignore[union-attr]
    store.fail(old.id, "late error")
    assert store.get(old.id).state == states.SUPERSEDED  # type: ignore[union-attr]


# --- repository config: base branch only ----------------------------------------------------


@pytest.fixture
def repo_with_config(make_repo: MakeRepo, open_workspace: OpenWorkspace) -> Any:
    def build(base_config: str | None, head_config: str | None) -> Any:
        repo = make_repo("cfg")
        repo.write("app.py", "x = 1\n")
        if base_config is not None:
            repo.write(CONFIG_PATH, base_config)
        repo.commit("base")
        repo.branch("pr")
        repo.write("app.py", "x = 2\n")
        if head_config is not None:
            repo.write(CONFIG_PATH, head_config)
        repo.commit("pr")
        return open_workspace(repo)[1]

    return build


def test_the_config_comes_from_the_base_branch_never_the_head(repo_with_config: Any) -> None:
    workspace = repo_with_config(
        "ignore_paths:\n  - docs/**\nmax_comments: 3\n", "enabled: false\n"
    )
    config, warnings = load_repo_config(workspace)
    assert (
        warnings == [] and config.enabled
    )  # the PR's attempt to switch the service off is ignored
    assert config.ignore_paths == ["docs/**"] and config.max_comments == 3


def test_a_config_added_only_by_the_pull_request_is_ignored(repo_with_config: Any) -> None:
    config, warnings = load_repo_config(repo_with_config(None, "ignore_paths:\n  - '**'\n"))
    assert config == RepoConfig() and warnings == []


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("enabled: maybe\n", "invalid"),
        ("unknown_key: 1\n", "invalid"),  # a typo can't silently loosen anything
        ("max_comments: 50\n", "invalid"),  # D17's cap is a ceiling
        ("reviewer: a9\n", "invalid"),
        ("- a\n- b\n", "invalid"),
        ("a: [unclosed\n", "invalid"),
        ("x: " + "y" * 30_000 + "\n", "over 20000 bytes"),
    ],
)
def test_a_bad_config_means_defaults_and_a_warning(
    repo_with_config: Any, text: str, why: str
) -> None:
    config, warnings = load_repo_config(repo_with_config(text, None))
    assert config == RepoConfig()
    assert len(warnings) == 1 and why in warnings[0]


def test_ignore_patterns_drop_files_from_the_diff(
    repo_with_config: Any, make_repo: MakeRepo, open_workspace: OpenWorkspace
) -> None:
    repo = make_repo("ign")
    for path in ("src/a.py", "docs/guide.md", "gen/out.py", "README.md"):
        repo.write(path, "1\n")
    repo.commit("base")
    repo.branch("pr")
    for path in ("src/a.py", "docs/guide.md", "gen/out.py", "README.md"):
        repo.write(path, "2\n")
    repo.commit("pr")
    diff = open_workspace(repo)[1].diff()
    kept, left_out = apply_ignore(diff, ["docs/**", "gen/*.py", "*.md"])
    assert [f.path for f in kept.files] == ["src/a.py"]
    assert sorted(left_out) == ["README.md", "docs/guide.md", "gen/out.py"]
    assert apply_ignore(diff, [])[0] is diff
