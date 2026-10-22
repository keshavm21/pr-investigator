"""Who may review a repository's runs: people with write access to that repository.

Checked with the App's own credentials (a metadata-only installation token), at most once per
five minutes per person and repository, and on every run page, not only at login.
"""

import time

import httpx

from pr_investigator.errors import GitHubError
from pr_investigator.service.app_auth import API, AppAuth

WRITE_ROLES = {"admin", "write", "maintain"}


class AccessChecker:
    def __init__(
        self,
        auth: AppAuth,
        *,
        transport: httpx.BaseTransport | None = None,
        ttl: float = 300.0,
    ) -> None:
        self._auth = auth
        self._http = httpx.Client(base_url=API, transport=transport, timeout=30.0)
        self._ttl = ttl
        self._cache: dict[tuple[str, str], tuple[float, bool]] = {}

    def can_write(self, installation_id: int, repo: str, login: str) -> bool:
        key = (repo.lower(), login.lower())
        cached = self._cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < self._ttl:
            return cached[1]
        allowed = self._ask(installation_id, repo, login)
        self._cache[key] = (time.monotonic(), allowed)
        return allowed

    def _ask(self, installation_id: int, repo: str, login: str) -> bool:
        owner, name = repo.split("/", 1)
        token = self._auth.reader_token(installation_id, name).token
        try:
            response = self._http.get(
                f"/repos/{owner}/{name}/collaborators/{login}/permission",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"couldn't check access: {exc}") from exc
        if response.status_code == 404:
            return False  # not a collaborator
        if response.status_code != 200:
            raise GitHubError(f"GitHub returned {response.status_code} checking access")
        data = response.json()
        return data.get("permission") in WRITE_ROLES or data.get("role_name") in WRITE_ROLES
