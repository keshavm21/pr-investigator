"""PR reference parsing and the GitHub REST client, against a mock transport."""

from typing import Any

import httpx
import pytest

from pr_investigator.errors import GitHubError
from pr_investigator.github import GitHubClient, parse_pr_reference


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/acme/widgets/pull/42",
        "https://github.com/acme/widgets/pull/42/files",
        "acme/widgets#42",
        "  acme/widgets#42  ",
    ],
)
def test_parses_pr_references(text: str) -> None:
    ref = parse_pr_reference(text)
    assert (ref.owner, ref.repo, ref.number) == ("acme", "widgets", 42)
    assert ref.url == "https://github.com/acme/widgets/pull/42"


@pytest.mark.parametrize(
    "text",
    ["https://gitlab.com/acme/widgets/pull/1", "acme/widgets", "http://github.com/a/b/pull/1"],
)
def test_rejects_other_references(text: str) -> None:
    with pytest.raises(GitHubError):
        parse_pr_reference(text)


def pr_payload(**base_repo: Any) -> dict[str, Any]:
    repo = {"private": False, "clone_url": "https://github.com/acme/widgets.git"} | base_repo
    return {
        "title": "Add search",
        "body": None,
        "user": {"login": "octocat"},
        "state": "open",
        "draft": False,
        "html_url": "https://github.com/acme/widgets/pull/42",
        "base": {"ref": "main", "sha": "a" * 40, "repo": repo},
        "head": {"ref": "feature", "sha": "b" * 40},
    }


def client_returning(status: int, payload: Any, seen: list[httpx.Request]) -> GitHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload, headers={"x-ratelimit-reset": "123"})

    return GitHubClient("token-123", transport=httpx.MockTransport(handler))


def test_fetches_and_maps_pull_request_metadata() -> None:
    seen: list[httpx.Request] = []
    info = client_returning(200, pr_payload(), seen).get_pull_request(
        parse_pr_reference("acme/widgets#42")
    )
    assert seen[0].url.path == "/repos/acme/widgets/pulls/42"
    assert seen[0].headers["Authorization"] == "Bearer token-123"
    assert info.body == "" and info.author == "octocat"
    assert (info.base_sha, info.head_sha) == ("a" * 40, "b" * 40)
    assert not info.base_repo_private


@pytest.mark.parametrize(("status", "message"), [(404, "404"), (403, "rate limit"), (500, "500")])
def test_reports_http_errors(status: int, message: str) -> None:
    client = client_returning(status, {"message": "nope"}, [])
    with pytest.raises(GitHubError, match=message):
        client.get_pull_request(parse_pr_reference("acme/widgets#42"))


def test_refuses_unexpected_clone_urls() -> None:
    payload = pr_payload(clone_url="file:///etc/repo.git")
    client = client_returning(200, payload, [])
    with pytest.raises(GitHubError, match="unexpected URL"):
        client.get_pull_request(parse_pr_reference("acme/widgets#42"))
