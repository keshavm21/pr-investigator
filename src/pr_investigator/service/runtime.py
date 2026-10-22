"""The control plane's dependencies, built once from the service settings."""

from dataclasses import dataclass

import httpx

from pr_investigator.config import Settings
from pr_investigator.service.access import AccessChecker
from pr_investigator.service.app_auth import AppAuth
from pr_investigator.service.config import ServiceSettings
from pr_investigator.service.oauth import OAuthClient
from pr_investigator.service.store import ServiceStore


@dataclass
class ServiceRuntime:
    settings: ServiceSettings
    store: ServiceStore
    auth: AppAuth
    oauth: OAuthClient
    access: AccessChecker
    github_transport: httpx.BaseTransport | None = None  # tests only

    @property
    def secure_cookies(self) -> bool:
        return self.settings.base_url.startswith("https://")


def build_runtime(service: ServiceSettings, settings: Settings) -> ServiceRuntime:
    from pr_investigator.errors import ConfigError

    service.require_control_plane()
    if not service.oauth_client_id or service.oauth_client_secret is None:
        raise ConfigError("the web app needs GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET")
    auth = AppAuth(service.app_id or "", service.private_key_pem())
    return ServiceRuntime(
        settings=service,
        store=ServiceStore(settings.service_db),
        auth=auth,
        oauth=OAuthClient(
            service.oauth_client_id,
            service.oauth_client_secret.get_secret_value(),
            service.base_url,
        ),
        access=AccessChecker(auth),
    )
