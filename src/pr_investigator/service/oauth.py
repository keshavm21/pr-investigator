"""Reviewer login with GitHub (the App's OAuth web flow). Used only to learn who the reviewer is.

The user access token is used for one request (`GET /user`) and then dropped: nothing about the
reviewer's GitHub account is stored beyond the login name in the session.
"""

from urllib.parse import urlencode

import httpx

from pr_investigator.errors import GitHubError

AUTHORIZE = "https://github.com/login/oauth/authorize"
TOKEN = "https://github.com/login/oauth/access_token"
USER = "https://api.github.com/user"


class OAuthClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._id = client_id
        self._secret = client_secret
        self._callback = base_url.rstrip("/") + "/oauth/callback"
        self._http = httpx.Client(transport=transport, timeout=30.0)

    def close(self) -> None:
        self._http.close()

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._id,
                "redirect_uri": self._callback,
                "state": state,
                "allow_signup": "false",
            }
        )
        return f"{AUTHORIZE}?{query}"

    def login_for_code(self, code: str) -> str:
        """Exchange the callback's code, ask GitHub who it is, and forget the token."""
        try:
            response = self._http.post(
                TOKEN,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self._id,
                    "client_secret": self._secret,
                    "code": code,
                    "redirect_uri": self._callback,
                },
            )
            token = response.json().get("access_token") if response.status_code == 200 else None
            if not isinstance(token, str):
                raise GitHubError("GitHub didn't give an access token for that code")
            user = self._http.get(
                USER,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubError(f"GitHub sign-in failed: {exc}") from exc
        login = user.json().get("login") if user.status_code == 200 else None
        if not isinstance(login, str) or not login:
            raise GitHubError("GitHub didn't say who signed in")
        return login
