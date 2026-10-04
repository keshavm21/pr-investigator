"""Minimal GitHub REST client: parse PR references and fetch PR metadata.

Phase 1 needs a single endpoint, so this uses httpx directly rather than a full SDK.
"""

import re
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from pr_investigator.domain import PullRequestRef
from pr_investigator.errors import GitHubError

_NAME = r"[A-Za-z0-9_.-]+"
_PR_URL = re.compile(
    rf"^https://github\.com/(?P<owner>{_NAME})/(?P<repo>{_NAME})/pull/(?P<number>\d+)(?:[/?#].*)?$"
)
_PR_SHORT = re.compile(rf"^(?P<owner>{_NAME})/(?P<repo>{_NAME})#(?P<number>\d+)$")


def parse_pr_reference(text: str) -> PullRequestRef:
    """Accept `https://github.com/owner/repo/pull/123` or `owner/repo#123`."""
    text = text.strip()
    match = _PR_URL.match(text) or _PR_SHORT.match(text)
    if match is None:
        raise GitHubError(
            f"not a pull request reference: {text!r} "
            "(expected https://github.com/OWNER/REPO/pull/N or OWNER/REPO#N)"
        )
    return PullRequestRef(
        owner=match["owner"], repo=match["repo"].removesuffix(".git"), number=int(match["number"])
    )


class PullRequestInfo(BaseModel):
    ref: PullRequestRef
    title: str
    body: str
    author: str
    state: str
    draft: bool
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    base_repo_private: bool
    clone_url: str
    html_url: str


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pr-investigator",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=base_url, headers=headers, transport=transport, timeout=timeout
        )

    def close(self) -> None:
        self._http.close()

    def get_pull_request(self, ref: PullRequestRef) -> PullRequestInfo:
        data = self._get_json(f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}")
        try:
            base, head = data["base"], data["head"]
            info = PullRequestInfo(
                ref=ref,
                title=data["title"],
                body=data.get("body") or "",
                author=(data.get("user") or {}).get("login", "unknown"),
                state=data["state"],
                draft=bool(data.get("draft", False)),
                base_ref=base["ref"],
                base_sha=base["sha"],
                head_ref=head["ref"],
                head_sha=head["sha"],
                base_repo_private=bool(base["repo"]["private"]),
                clone_url=base["repo"]["clone_url"],
                html_url=data["html_url"],
            )
        except (KeyError, TypeError) as exc:
            raise GitHubError(f"unexpected pull request payload: missing {exc}") from exc
        _check_clone_url(info.clone_url)
        return info

    def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = self._http.get(path)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        if response.status_code == 404:
            raise GitHubError(
                f"GitHub returned 404 for {path}: the PR doesn't exist, or the repository is "
                "private and the token can't see it"
            )
        if response.status_code in (403, 429):
            reset = response.headers.get("x-ratelimit-reset")
            hint = f" (rate limit resets at unix time {reset})" if reset else ""
            raise GitHubError(
                f"GitHub refused the request ({response.status_code}){hint}. "
                "Setting GITHUB_TOKEN raises the limit."
            )
        if response.status_code != 200:
            raise GitHubError(f"GitHub returned {response.status_code} for {path}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise GitHubError(f"unexpected response shape from {path}")
        return payload


def _check_clone_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise GitHubError(f"refusing to clone from unexpected URL {url!r}")
