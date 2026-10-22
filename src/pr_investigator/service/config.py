"""Service settings, from the environment. Secrets are never logged or rendered."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from pr_investigator.errors import ConfigError

# The analysis worker must not hold any of these (it refuses to start if one is set).
FORBIDDEN_IN_WORKER = (
    "GITHUB_TOKEN",
    "PRI_GITHUB_TOKEN",
    "GITHUB_APP_ID",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_APP_PRIVATE_KEY_FILE",
    "GITHUB_WEBHOOK_SECRET",
    "GITHUB_OAUTH_CLIENT_SECRET",
)


class ServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_id: str | None = Field(default=None, validation_alias="GITHUB_APP_ID")
    private_key: SecretStr | None = Field(default=None, validation_alias="GITHUB_APP_PRIVATE_KEY")
    private_key_file: Path | None = Field(
        default=None, validation_alias="GITHUB_APP_PRIVATE_KEY_FILE"
    )
    webhook_secret: SecretStr | None = Field(default=None, validation_alias="GITHUB_WEBHOOK_SECRET")
    oauth_client_id: str | None = Field(default=None, validation_alias="GITHUB_OAUTH_CLIENT_ID")
    oauth_client_secret: SecretStr | None = Field(
        default=None, validation_alias="GITHUB_OAUTH_CLIENT_SECRET"
    )
    # The public URL of the web app, for the OAuth callback and secure cookies (https).
    base_url: str = Field(default="http://127.0.0.1:8000", validation_alias="PRI_BASE_URL")

    # Admission policy. Only allowlisted repositories ("owner/name") are analyzed.
    allowed_repos: str = Field(default="", validation_alias="PRI_ALLOWED_REPOS")
    allow_private: bool = Field(default=False, validation_alias="PRI_ALLOW_PRIVATE")
    max_changed_files: int = Field(default=100, ge=1, validation_alias="PRI_MAX_CHANGED_FILES")
    max_changed_lines: int = Field(default=5_000, ge=1, validation_alias="PRI_MAX_CHANGED_LINES")
    daily_runs_per_installation: int = Field(
        default=20, ge=1, validation_alias="PRI_DAILY_RUNS_PER_INSTALLATION"
    )
    reviewer: str = Field(default="a2", validation_alias="PRI_SERVICE_REVIEWER")
    max_requests_per_run: int = Field(default=30, ge=1, validation_alias="PRI_MAX_REQUESTS_PER_RUN")

    @property
    def allowlist(self) -> set[str]:
        return {r.strip().lower() for r in self.allowed_repos.split(",") if r.strip()}

    def private_key_pem(self) -> str:
        if self.private_key is not None:
            return self.private_key.get_secret_value().replace("\\n", "\n")
        if self.private_key_file is not None:
            try:
                return self.private_key_file.read_text(encoding="utf-8")
            except OSError as exc:
                raise ConfigError(f"can't read the GitHub App private key: {exc}") from exc
        raise ConfigError("set GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_FILE")

    def require_control_plane(self) -> None:
        missing = [
            name
            for name, value in (
                ("GITHUB_APP_ID", self.app_id),
                ("GITHUB_WEBHOOK_SECRET", self.webhook_secret),
            )
            if not value
        ]
        if missing:
            raise ConfigError(f"the service needs {', '.join(missing)}")
        self.private_key_pem()
