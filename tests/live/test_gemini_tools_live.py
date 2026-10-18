"""A real Gemini tool-calling round trip: a function call, our tool result, a final answer.

Checks what offline tests can't: that Gemini accepts our function declarations, our replay of
its own turn (thought signatures included) and our tool-result format. Skipped unless
PRI_LIVE_TESTS=1, and capped at 3 requests. Run with:
    PRI_LIVE_TESTS=1 uv run pytest tests/live/test_gemini_tools_live.py -m live -s
"""

import os

import pytest
from pydantic import BaseModel, Field

from pr_investigator.config import CacheMode, Settings
from pr_investigator.domain import DataClass
from pr_investigator.llm.factory import build_client
from pr_investigator.llm.guards import RequestBudget
from pr_investigator.llm.types import LLMRequest, Message, Role, ToolSpec
from tests.support import REPO_ROOT

# Read before the autouse fixture strips credentials from the environment.
_LIVE = os.environ.get("PRI_LIVE_TESTS") == "1"
_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

STORED_VALUE = "violet-42"  # a value the model can't guess without calling the tool


class LookupArgs(BaseModel):
    key: str = Field(description="The key to look up.")


@pytest.mark.live
@pytest.mark.skipif(not _LIVE, reason="set PRI_LIVE_TESTS=1 to call the real Gemini API")
async def test_gemini_tool_call_round_trip() -> None:
    overrides: dict[str, object] = {
        "_env_file": REPO_ROOT / ".env",
        "llm_provider": "gemini",
        "llm_cache_mode": CacheMode.OFF,
    }
    if _KEY:
        overrides["gemini_api_key"] = _KEY
    settings = Settings(**overrides)  # type: ignore[arg-type]
    client = build_client(settings, budget=RequestBudget(3))
    lookup = ToolSpec(
        name="lookup_value",
        description="Look up the stored value for a key. Call this to answer questions about keys.",
        parameters=LookupArgs.model_json_schema(),
    )
    messages = [
        Message.user(
            "What is the stored value for the key 'alpha'? Use the lookup_value tool, "
            "then reply with only the value."
        )
    ]
    for _ in range(3):
        response = await client.generate(
            LLMRequest(
                stage="live_tool_check",
                data_class=DataClass.TEST,
                system="You answer questions by calling the tools you are given.",
                messages=messages,
                tools=[lookup],
                max_output_tokens=1024,
            )
        )
        messages.append(response.to_message())
        if not response.tool_calls:
            break
        for call in response.tool_calls:
            known = call.arguments.get("key") == "alpha"
            messages.append(
                Message.tool_result(
                    call, STORED_VALUE if known else "unknown key", is_error=not known
                )
            )
    assert any(m.role is Role.TOOL for m in messages), "the model never called the tool"
    assert STORED_VALUE in messages[-1].text, f"final answer: {messages[-1].text!r}"
    print(f"\n{settings.llm_model}: {len(messages)} messages, final answer {messages[-1].text!r}")
