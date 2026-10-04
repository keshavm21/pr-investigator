"""Settings, read from environment variables and an optional `.env` file.

Secrets (API keys, tokens) are `SecretStr` so they never appear in reprs or logs.
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


class CacheMode(StrEnum):
    """How the LLM response cache behaves.

    - off: always call the provider.
    - read_write: serve cached responses, call the provider on a miss and store the result.
    - replay: serve cached responses only; a miss is an error (deterministic tests and re-scoring).
    """

    OFF = "off"
    READ_WRITE = "read_write"
    REPLAY = "replay"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRI_", env_file=".env", extra="ignore")

    llm_provider: Literal["gemini", "fake"] = "gemini"
    llm_model: str = DEFAULT_GEMINI_MODEL
    llm_requests_per_minute: int = Field(default=5, ge=1)
    llm_max_retries: int = Field(default=3, ge=0)
    llm_cache_mode: CacheMode = CacheMode.READ_WRITE
    allow_paid_providers: bool = False

    # "free" means: no billing on the key's project, and the provider may use inputs for training.
    gemini_tier: Literal["free", "paid"] = "free"
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY", "PRI_GEMINI_API_KEY"),
    )
    github_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GITHUB_TOKEN", "PRI_GITHUB_TOKEN")
    )

    data_dir: Path = Path(".pri")
    max_diff_chars: int = Field(default=200_000, ge=1_000)

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache" / "llm"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"
