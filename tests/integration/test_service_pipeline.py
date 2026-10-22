"""The fetch and analyze roles over a local repository standing in for GitHub."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from pr_investigator import ingest
from pr_investigator.config import Settings
from pr_investigator.domain import DataClass
from pr_investigator.errors import ConfigError, PRIError
from pr_investigator.github import GitHubClient
from pr_investigator.service import pipeline
from pr_investigator.service import store as states
from pr_investigator.service.app_auth import AppAuth
from pr_investigator.service.store import Job, ServiceStore
from tests.support import GitRepo, MakeRepo
from tests.support_service import PRIVATE_PEM, service_settings
from tests.unit.test_publish import PR_PATH, _pr


@pytest.fixture(autouse=True)
def fast_fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRI_LLM_REQUESTS_PER_MINUTE", "1000")


class Fixture:
    def __init__(self, repo: GitRepo, settings: Settings, store: ServiceStore) -> None:
        self.repo, self.settings, self.store = repo, settings, store
        self.token_requests: list[dict[str, Any]] = []
        self.fetched_with: list[str | None] = []
        self.head = repo.git("rev-parse", "pr").strip()
        self.reported_head = self.head
        self.private = False

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/access_tokens"):
                body = json.loads(request.content)
                self.token_requests.append(body)
                return httpx.Response(
                    201,
                    json={
                        "token": "ghs_secret",
                        "expires_at": "x",
                        "permissions": body["permissions"],
                    },
                )
            if request.url.path == PR_PATH:
                payload = _pr(self.reported_head)
                payload["base"]["repo"]["private"] = self.private
                return httpx.Response(200, json=payload)
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    def auth(self) -> AppAuth:
        return AppAuth("12345", PRIVATE_PEM, transport=self.transport())

    def job(self) -> Job:
        job, _ = self.store.enqueue("acme/app", 7, self.head, 99, "d1")
        claimed = self.store.claim(states.QUEUED, states.FETCHING)
        assert claimed is not None and claimed.id == job.id
        return claimed

    def fetch(self, monkeypatch: pytest.MonkeyPatch, job: Job) -> str:
        def local(info: Any, workspaces_dir: Path, *, token: str | None = None) -> Any:
            self.fetched_with.append(token)
            return ingest.from_local(
                self.repo.path,
                "main",
                "pr",
                title=info.title,
                data_class=DataClass.TEST,
                workspaces_dir=workspaces_dir,
            )

        monkeypatch.setattr(pipeline.ingest, "from_github", local)
        client = GitHubClient("t", transport=self.transport())
        return pipeline.fetch_job(
            job, self.store, self.auth(), self.settings, service_settings(), github=client
        )


@pytest.fixture
def fx(make_repo: MakeRepo, settings: Settings, tmp_path: Path) -> Fixture:
    repo = make_repo("remote")
    repo.write("app.py", "x = 1\n")
    repo.write("docs/guide.md", "old\n")
    repo.write(".pr-investigator.yml", "ignore_paths:\n  - docs/**\n")
    repo.commit("base")
    repo.branch("pr")
    repo.write("app.py", "x = 2\n")
    repo.write("docs/guide.md", "new\n")
    repo.write(".pr-investigator.yml", "enabled: false\n")  # the PR tries to disable the service
    repo.commit("pr")
    return Fixture(repo, settings, ServiceStore(tmp_path / "service.sqlite"))


def test_the_fetcher_uses_a_narrow_token_and_hands_over_a_workspace_not_a_credential(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = fx.job()
    assert fx.fetch(monkeypatch, job) == states.READY
    assert fx.token_requests == [{"repositories": ["app"], "permissions": {"contents": "read"}}]
    assert fx.fetched_with == ["ghs_secret"]  # used for the fetch only
    stored = fx.store.get(job.id)
    assert stored is not None and stored.state == states.READY
    assert "ghs_secret" not in json.dumps(stored.payload)  # never stored
    assert stored.payload["config"]["ignore_paths"] == ["docs/**"]  # from the base branch
    assert stored.payload["config"]["enabled"] is True  # the PR's own config was ignored
    assert stored.payload["target"]["pull_request"] is None  # (the local stand-in has none)
    assert Path(stored.payload["workspace"]["mirror"]).is_dir()


def test_a_job_whose_head_moved_is_dropped_before_any_fetch(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx.reported_head = "9" * 40  # the PR got a newer push
    job = fx.job()
    assert fx.fetch(monkeypatch, job) == states.SUPERSEDED
    assert fx.fetched_with == []  # nothing was fetched


def test_private_repositories_are_skipped_unless_allowed(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx.private = True
    job = fx.job()
    assert fx.fetch(monkeypatch, job) == states.SKIPPED
    assert fx.fetched_with == []


def test_a_base_branch_config_can_switch_the_service_off_for_a_repository(
    fx: Fixture,
    make_repo: MakeRepo,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo("off")
    repo.write(".pr-investigator.yml", "enabled: false\n")
    repo.write("a.py", "1\n")
    repo.commit("base")
    repo.branch("pr")
    repo.write("a.py", "2\n")
    repo.commit("pr")
    off = Fixture(repo, settings, fx.store)
    assert off.fetch(monkeypatch, off.job()) == states.SKIPPED


def test_the_analyzer_reviews_the_workspace_applies_the_config_and_records_the_run(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx.fetch(monkeypatch, fx.job())
    claimed = fx.store.claim(states.READY, states.ANALYZING)
    assert claimed is not None
    assert (
        pipeline.analyze_job(claimed, fx.store, fx.settings, service_settings())
        == states.AWAITING_REVIEW
    )
    done = fx.store.get(claimed.id)
    assert done is not None and done.state == states.AWAITING_REVIEW and done.run_id
    run = json.loads((fx.settings.runs_dir / done.run_id / "run.json").read_text())
    assert run["reviewer"] == "a2"
    assert run["coverage"]["skipped"]["docs/guide.md"] == "ignored by .pr-investigator.yml"
    assert (fx.settings.runs_dir / done.run_id / "trace.jsonl").is_file()


def test_an_analysis_that_finishes_after_a_newer_push_is_marked_superseded(
    fx: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx.fetch(monkeypatch, fx.job())
    claimed = fx.store.claim(states.READY, states.ANALYZING)
    assert claimed is not None
    fx.store.enqueue("acme/app", 7, "d" * 40, 99, "d2")  # a new push arrives meanwhile
    result = pipeline.analyze_job(claimed, fx.store, fx.settings, service_settings())
    assert result == states.SUPERSEDED
    old = fx.store.get(claimed.id)
    assert old is not None and old.state == states.SUPERSEDED and old.run_id  # linked, not lost


def test_worker_loops_move_jobs_and_report_failures(fx: Fixture) -> None:
    broken = AppAuth(
        "12345", PRIVATE_PEM, transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    )
    assert not pipeline.fetch_once(fx.store, broken, fx.settings, service_settings())  # no work
    job, _ = fx.store.enqueue("acme/app", 7, fx.head, 99, "d1")
    assert pipeline.fetch_once(fx.store, broken, fx.settings, service_settings())
    failed = fx.store.get(job.id)
    assert failed is not None and failed.state == states.FAILED
    assert "fetch failed" in (failed.error or "") and "500" in (failed.error or "")
    assert not pipeline.fetch_once(fx.store, broken, fx.settings, service_settings())  # drained


def test_the_analysis_worker_refuses_to_start_with_github_credentials(settings: Settings) -> None:
    pipeline.assert_no_github_credentials(settings, {"GEMINI_API_KEY": "k"})
    for name in (
        "GITHUB_TOKEN",
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_WEBHOOK_SECRET",
    ):
        with pytest.raises(ConfigError, match=name):
            pipeline.assert_no_github_credentials(settings, {name: "x"})
    from pydantic import SecretStr

    with pytest.raises(ConfigError, match=r"GITHUB_TOKEN \(from .env\)"):
        pipeline.assert_no_github_credentials(
            settings.model_copy(update={"github_token": SecretStr("t")}), {}
        )


def test_the_analyzer_never_touches_a_workspace_outside_the_workspaces_directory(
    settings: Settings,
) -> None:
    payload = {
        "workspace": {
            "mirror": "/etc",
            "base_sha": "a",
            "head_sha": "b",
            "merge_base_sha": "c",
            "checkout_root": "/tmp/x",
        }
    }
    with pytest.raises(PRIError, match="outside the workspaces directory"):
        pipeline.workspace_from(payload, settings.workspaces_dir)
