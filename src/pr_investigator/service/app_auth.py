"""GitHub App authentication: a short-lived JWT, then one-hour installation tokens.

Each job gets a token narrowed to one repository and the minimum permission: the fetcher gets
`contents: read`, the publisher `pull_requests: write` (docs/architecture.md §8). Tokens are
returned to the caller and never logged or stored.
"""

import time
from dataclasses import dataclass

import httpx
import jwt

from pr_investigator.errors import GitHubError

API = "https://api.github.com"
JWT_LIFETIME = 9 * 60  # GitHub allows at most 10 minutes
_RANK = {"read": 1, "write": 2, "admin": 3}


@dataclass(frozen=True)
class InstallationToken:
    token: str
    expires_at: str


def app_jwt(app_id: str, private_key_pem: str, *, now: float | None = None) -> str:
    """The App's identity for one request. Issued 60 seconds in the past, as GitHub advises,
    to tolerate clock drift."""
    issued = int(now if now is not None else time.time())
    claims = {"iat": issued - 60, "exp": issued + JWT_LIFETIME, "iss": app_id}
    return jwt.encode(claims, private_key_pem, algorithm="RS256")


class AppAuth:
    def __init__(
        self,
        app_id: str,
        private_key_pem: str,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = API,
    ) -> None:
        self._app_id = app_id
        self._key = private_key_pem
        self._http = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=30.0,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "pr-investigator",
            },
        )

    def close(self) -> None:
        self._http.close()

    def installation_token(
        self, installation_id: int, repo: str, permissions: dict[str, str]
    ) -> InstallationToken:
        """A token for one repository ("name", without the owner) and the given permissions."""
        response = self._post(
            f"/app/installations/{installation_id}/access_tokens",
            {"repositories": [repo], "permissions": permissions},
        )
        data = response.json()
        token, expires = data.get("token"), data.get("expires_at")
        if not isinstance(token, str) or not isinstance(expires, str):
            raise GitHubError("unexpected installation token response")
        granted = data.get("permissions") or {}
        for name, level in permissions.items():
            if _RANK.get(str(granted.get(name)), 0) < _RANK[level]:
                raise GitHubError(f"the installation token doesn't grant {name}: {level}")
        return InstallationToken(token, expires)

    def fetcher_token(self, installation_id: int, repo: str) -> InstallationToken:
        return self.installation_token(installation_id, repo, {"contents": "read"})

    def publisher_token(self, installation_id: int, repo: str) -> InstallationToken:
        return self.installation_token(installation_id, repo, {"pull_requests": "write"})

    def reader_token(self, installation_id: int, repo: str) -> InstallationToken:
        """For checking a reviewer's access: metadata only."""
        return self.installation_token(installation_id, repo, {"metadata": "read"})

    def _post(self, path: str, body: dict[str, object]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {app_jwt(self._app_id, self._key)}"}
        try:
            response = self._http.post(path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        if response.status_code not in (200, 201):
            raise GitHubError(f"GitHub returned {response.status_code} for {path}")
        return response
