"""Shared test fixtures. Nothing here calls a network service."""

from collections.abc import Iterator
from importlib.util import find_spec
from pathlib import Path

import pytest

from tests.environment import broken_install_message

# Fail with an explanation instead of a bare ModuleNotFoundError from the first import below.
if find_spec("pr_investigator") is None:
    raise ImportError(broken_install_message())

from pr_investigator import ingest
from pr_investigator.config import CacheMode, Settings
from pr_investigator.domain import DataClass, ReviewTarget
from pr_investigator.workspace import Workspace
from tests.support import GitRepo, MakeRepo, OpenWorkspace


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep tests away from real credentials and config, so they can never call a live API.

    Tests run in an empty directory (no `.env` to pick up) with API keys removed and the
    offline provider selected. Live tests opt back in explicitly.
    """
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "PRI_GEMINI_API_KEY", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRI_LLM_PROVIDER", "fake")
    monkeypatch.setenv("PRI_DATA_DIR", str(tmp_path / "pri-data"))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def make_repo(tmp_path: Path) -> MakeRepo:
    def factory(name: str = "repo") -> GitRepo:
        return GitRepo(tmp_path / name)

    return factory


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="fake",
        llm_cache_mode=CacheMode.READ_WRITE,
        data_dir=tmp_path / "pri-data",
        gemini_api_key=None,
        github_token=None,
    )


@pytest.fixture
def open_workspace(tmp_path: Path) -> Iterator[OpenWorkspace]:
    """Review `pr` against `main` in a GitRepo, through the real local ingestion path."""
    opened: list[Workspace] = []

    def factory(repo: GitRepo) -> tuple[ReviewTarget, Workspace]:
        target, workspace = ingest.from_local(
            repo.path,
            "main",
            "pr",
            title="Test PR",
            data_class=DataClass.TEST,
            workspaces_dir=tmp_path / "workspaces",
        )
        opened.append(workspace)
        return target, workspace

    yield factory
    for workspace in opened:
        workspace.cleanup()
