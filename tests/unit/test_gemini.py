"""Gemini adapter mapping, tested against SDK types with a stub client. No network."""

from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from pr_investigator.domain import DataClass
from pr_investigator.llm.gemini import GeminiAdapter, build_config, gemini_schema, to_completion
from pr_investigator.llm.types import (
    Effort,
    LLMRateLimitError,
    LLMRequest,
    LLMRequestError,
    LLMTransientError,
    Message,
    StopReason,
    ToolSpec,
)
from pr_investigator.review.baseline import BaselineOutput


class Tricky(BaseModel):
    # Field names that collide with JSON-schema keywords must survive schema cleaning.
    default: str = "x"
    title: str | None = None


def make_request(**overrides: Any) -> LLMRequest:
    fields: dict[str, Any] = {
        "stage": "test",
        "data_class": DataClass.TEST,
        "system": "be brief",
        "messages": [Message.user("hello")],
    }
    fields.update(overrides)
    return LLMRequest(**fields)


def response(
    parts: list[types.Part] | None = None, finish: str | None = "STOP", **extra: Any
) -> types.GenerateContentResponse:
    candidate = types.Candidate(
        content=types.Content(role="model", parts=parts or []),
        finish_reason=finish,
    )
    return types.GenerateContentResponse(
        candidates=[candidate],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=20, thoughts_token_count=5
        ),
        **extra,
    )


def walk(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        return [node, *(d for v in node.values() for d in walk(v))]
    if isinstance(node, list):
        return [d for item in node for d in walk(item)]
    return []


def test_config_carries_system_prompt_limits_and_a_clean_schema() -> None:
    config = build_config(make_request(output_schema=BaselineOutput, max_output_tokens=1234))
    assert config.system_instruction == "be brief"
    assert config.max_output_tokens == 1234
    assert config.response_mime_type == "application/json"
    schema = config.response_json_schema
    assert isinstance(schema, dict)
    assert all("default" not in node for node in walk(schema))
    assert "findings" in schema["properties"]


def test_schema_cleaning_keeps_property_names() -> None:
    schema = gemini_schema(Tricky)
    assert set(schema["properties"]) == {"default", "title"}
    assert "default" not in schema["properties"]["default"]


def test_effort_maps_to_thinking_level_only_when_not_medium() -> None:
    assert build_config(make_request(effort=Effort.MEDIUM)).thinking_config is None
    assert build_config(make_request()).thinking_config is None
    high = build_config(make_request(effort=Effort.HIGH)).thinking_config
    assert high is not None and high.thinking_level == types.ThinkingLevel.HIGH


def test_text_parts_exclude_thoughts_and_usage_is_mapped() -> None:
    result = to_completion(
        response([types.Part(text="hidden", thought=True), types.Part(text='{"a": 1}')]), "m"
    )
    assert result.stop_reason is StopReason.END and result.text == '{"a": 1}'
    assert (result.usage.input_tokens, result.usage.output_tokens) == (100, 20)
    assert result.usage.thinking_tokens == 5


@pytest.mark.parametrize(
    ("finish", "expected"),
    [
        ("MAX_TOKENS", StopReason.MAX_TOKENS),
        ("SAFETY", StopReason.REFUSED),
        ("RECITATION", StopReason.REFUSED),
        ("MALFORMED_FUNCTION_CALL", StopReason.ERROR),
    ],
)
def test_finish_reasons_are_normalized(finish: str, expected: StopReason) -> None:
    assert to_completion(response([types.Part(text="x")], finish), "m").stop_reason is expected


def test_blocked_prompt_and_empty_candidates() -> None:
    blocked = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY")
    )
    result = to_completion(blocked, "m")
    assert result.stop_reason is StopReason.REFUSED and "SAFETY" in (result.stop_detail or "")
    empty = to_completion(types.GenerateContentResponse(candidates=[]), "m")
    assert empty.stop_reason is StopReason.ERROR


class StubModels:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def adapter_with(outcome: Any) -> tuple[GeminiAdapter, StubModels]:
    models = StubModels(outcome)
    stub = SimpleNamespace(aio=SimpleNamespace(models=models))
    return GeminiAdapter(model="gemini-test", api_key=None, client=stub), models


async def test_complete_sends_contents_and_maps_the_response() -> None:
    adapter, models = adapter_with(response([types.Part(text="hi")]))
    result = await adapter.complete(
        make_request(messages=[Message.user("q"), Message.assistant(""), Message.user("again")])
    )
    assert result.text == "hi" and result.provider == "gemini"
    contents = models.calls[0]["contents"]
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].text == "(empty response)"
    assert models.calls[0]["model"] == "gemini-test"


async def test_free_tier_is_marked_as_training_on_inputs() -> None:
    adapter, _ = adapter_with(response())
    assert adapter.capabilities.may_train_on_inputs and not adapter.capabilities.paid


async def test_rate_limit_errors_carry_the_retry_delay() -> None:
    error = genai_errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Quota exceeded",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "31s"}
                ],
            }
        },
    )
    adapter, _ = adapter_with(error)
    with pytest.raises(LLMRateLimitError) as raised:
        await adapter.complete(make_request())
    assert raised.value.retry_after == 31.0


async def test_other_errors_are_classified() -> None:
    bad_request = genai_errors.ClientError(400, {"error": {"code": 400, "message": "bad model"}})
    adapter, _ = adapter_with(bad_request)
    with pytest.raises(LLMRequestError, match="bad model"):
        await adapter.complete(make_request())
    server = genai_errors.ServerError(503, {"error": {"code": 503, "message": "overloaded"}})
    adapter, _ = adapter_with(server)
    with pytest.raises(LLMTransientError):
        await adapter.complete(make_request())


async def test_tool_calling_is_rejected_until_phase_two() -> None:
    adapter, models = adapter_with(response())
    spec = ToolSpec(name="t", description="d", parameters={"type": "object"})
    with pytest.raises(LLMRequestError):
        await adapter.complete(make_request(tools=[spec]))
    assert models.calls == []
