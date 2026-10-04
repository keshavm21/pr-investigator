"""GitHub PR ingestion against a mocked API and a local stand-in for the remote repository."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from pr_investigator import ingest
from pr_investigator import workspace as ws
from pr_investigator.domain import DataClass
from pr_investigator.errors import DataPolicyError
from pr_investigator.github import GitHubClient, parse_pr_reference
from pr_investigator.llm.client import ensure_data_allowed
from pr_investigator.llm.types import Capabilities
from tests.support import GitRepo, MakeRepo


@pytest.fixture
def remote(make_repo: MakeRepo, tmp_path: Path) -> tuple[Path, str, str]:
    """A bare 'GitHub' repository with main and a PR ref at refs/pull/7/head."""
    repo: GitRepo = make_repo("source")
    repo.write("lib.py", "VALUE = 1\n")
    base = repo.commit("base")
    repo.branch("feature")
    repo.write("lib.py", "VALUE = 2\n")
    head = repo.commit("feature")
    bare = tmp_path / "remote.git"
    ws.run_git(["init", "--bare", "--quiet", str(bare)])
    repo.git("push", "--quiet", str(bare), "main", "feature:refs/pull/7/head")
    return bare, base, head


def github_returning(base: str, head: str, private: bool) -> GitHubClient:
    payload: dict[str, Any] = {
        "title": "Bump value",
        "body": "",
        "user": {"login": "octocat"},
        "state": "open",
        "draft": False,
        "html_url": "https://github.com/acme/widgets/pull/7",
        "base": {
            "ref": "main",
            "sha": base,
            "repo": {"private": private, "clone_url": "https://github.com/acme/widgets.git"},
        },
        "head": {"ref": "feature", "sha": head},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    return GitHubClient(transport=transport)


def test_fetches_pr_head_from_refs_pull(
    remote: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, base, head = remote
    real_fetch = ws.fetch
    sources: list[str] = []

    def fetch_from_local(mirror: Path, source: str, refspecs: list[str], **kw: Any) -> None:
        sources.append(source)
        real_fetch(mirror, str(bare), refspecs, **kw)

    monkeypatch.setattr(ws, "fetch", fetch_from_local)
    info = github_returning(base, head, private=False).get_pull_request(
        parse_pr_reference("acme/widgets#7")
    )
    target, workspace = ingest.from_github(info, tmp_path / "workspaces")

    assert sources == ["https://github.com/acme/widgets.git"]
    assert (target.base_sha, target.head_sha, target.merge_base_sha) == (base, head, base)
    assert target.data_class is DataClass.PUBLIC and target.pull_request is not None
    (changed,) = workspace.diff().files
    assert changed.path == "lib.py" and changed.added_lines == 1


def test_private_prs_are_refused_before_fetching(remote: tuple[Path, str, str]) -> None:
    _bare, base, head = remote
    info = github_returning(base, head, private=True).get_pull_request(
        parse_pr_reference("acme/widgets#7")
    )
    free_tier = Capabilities(
        structured_output=True, tool_calling=True, may_train_on_inputs=True, paid=False
    )
    with pytest.raises(DataPolicyError):
        ensure_data_allowed(ingest.data_class_for(info), free_tier, "gemini")
