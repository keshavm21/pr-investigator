"""Fixtures for the hosted-service tests: a throwaway App key and settings. Nothing here
touches the network."""

import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from pr_investigator.service.config import ServiceSettings

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)
SECRET = "whsec-test"
HEAD = "c" * 40
BASE = "b" * 40


def service_settings(**fields: Any) -> ServiceSettings:
    values: dict[str, Any] = {
        "app_id": "12345",
        "private_key": SecretStr(PRIVATE_PEM),
        "webhook_secret": SecretStr(SECRET),
        "oauth_client_id": "client-id",
        "oauth_client_secret": SecretStr("client-secret"),
        "base_url": "https://pri.example.com",
        "allowed_repos": "acme/app",
    }
    return ServiceSettings(_env_file=None, **{**values, **fields})  # type: ignore[call-arg]


def pr_event(
    action: str = "opened",
    *,
    repo: str = "acme/app",
    number: int = 7,
    head: str = HEAD,
    installation: int = 99,
    draft: bool = False,
    private: bool = False,
    files: int = 3,
    lines: int = 40,
    state: str = "open",
) -> bytes:
    return json.dumps(
        {
            "action": action,
            "number": number,
            "pull_request": {
                "number": number,
                "state": state,
                "draft": draft,
                "head": {"sha": head},
                "changed_files": files,
                "additions": lines,
                "deletions": 0,
            },
            "repository": {"full_name": repo, "private": private},
            "installation": {"id": installation},
        }
    ).encode()
