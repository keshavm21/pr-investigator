"""One real Gemini call, to confirm the key, model and structured output work together.

Skipped unless PRI_LIVE_TESTS=1. Uses one request of free-tier quota. Run with:
    PRI_LIVE_TESTS=1 uv run pytest tests/live -m live
"""

import os

import pytest
from pydantic import BaseModel

from pr_investigator.config import CacheMode, Settings
from pr_investigator.domain import DataClass
from pr_investigator.llm.factory import build_client
from pr_investigator.llm.types import LLMRequest, Message
from tests.support import REPO_ROOT

# Read before the autouse fixture strips credentials from the environment.
_LIVE = os.environ.get("PRI_LIVE_TESTS") == "1"
_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


class Greeting(BaseModel):
    greeting: str


@pytest.mark.live
@pytest.mark.skipif(not _LIVE, reason="set PRI_LIVE_TESTS=1 to call the real Gemini API")
async def test_gemini_structured_output(tmp_path: object) -> None:
    overrides: dict[str, object] = {
        "_env_file": REPO_ROOT / ".env",
        "llm_provider": "gemini",
        "llm_cache_mode": CacheMode.OFF,
    }
    if _KEY:
        overrides["gemini_api_key"] = _KEY
    settings = Settings(**overrides)  # type: ignore[arg-type]
    client = build_client(settings)
    response = await client.generate(
        LLMRequest(
            stage="live_smoke",
            data_class=DataClass.TEST,
            system="Reply with a JSON object.",
            messages=[Message.user("Say hello in three words.")],
            output_schema=Greeting,
            max_output_tokens=1024,
        )
    )
    assert isinstance(response.parsed, Greeting) and response.parsed.greeting
    print(f"\n{settings.llm_model}: {response.parsed.greeting!r}, usage {response.usage}")
