"""Gemini adapter mapping, tested against SDK types with a stub client. No network."""

from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import _extra_utils, types
from google.genai import errors as genai_errors
from pydantic import BaseModel, ValidationError

from pr_investigator.config import CacheMode
from pr_investigator.domain import DataClass
from pr_investigator.llm.cache import CachingAdapter, ResponseCache
from pr_investigator.llm.fake import ScriptedAdapter
from pr_investigator.llm.gemini import (
    GeminiAdapter,
    build_config,
    gemini_schema,
    to_completion,
    to_contents,
)
from pr_investigator.llm.types import (
    Effort,
    LLMRateLimitError,
    LLMRequest,
    LLMRequestError,
    LLMTransientError,
    Message,
    Role,
    StopReason,
    ToolCall,
)
from pr_investigator.review.baseline import BaselineOutput
from pr_investigator.tools import tool_specs

SIGNATURE = bytes(range(256))  # every byte value, including invalid UTF-8


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
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "31s"},
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
                        ],
                    },
                ],
            }
        },
    )
    adapter, _ = adapter_with(error)
    with pytest.raises(LLMRateLimitError) as raised:
        await adapter.complete(make_request())
    assert raised.value.retry_after == 31.0
    # The message names the exhausted quota, so a stop reason says which limit was hit.
    assert "GenerateRequestsPerDayPerProjectPerModel-FreeTier" in str(raised.value)
    assert "Quota exceeded" in str(raised.value)


async def test_other_errors_are_classified() -> None:
    bad_request = genai_errors.ClientError(400, {"error": {"code": 400, "message": "bad model"}})
    adapter, _ = adapter_with(bad_request)
    with pytest.raises(LLMRequestError, match="bad model"):
        await adapter.complete(make_request())
    server = genai_errors.ServerError(503, {"error": {"code": 503, "message": "overloaded"}})
    adapter, _ = adapter_with(server)
    with pytest.raises(LLMTransientError):
        await adapter.complete(make_request())


# --- tool calling ---------------------------------------------------------------------------


def signature_turn() -> types.Content:
    """A model turn as Gemini returns it: a thought summary, then calls carrying a signature."""
    return types.Content(
        role="model",
        parts=[
            types.Part(text="I should read the file.", thought=True),
            types.Part(
                function_call=types.FunctionCall(
                    id="call-abc", name="read_file", args={"path": "app/a.py", "start_line": 3}
                ),
                thought_signature=SIGNATURE,
            ),
            types.Part(function_call=types.FunctionCall(name="search_code", args={"pattern": "x"})),
        ],
    )


def test_config_declares_tools_with_clean_schemas_and_disables_afc() -> None:
    specs = tool_specs()
    config = build_config(make_request(tools=specs))
    assert config.tools is not None
    (tool,) = config.tools
    declarations = tool.function_declarations or []
    assert [d.name for d in declarations] == [spec.name for spec in specs]
    for declaration in declarations:
        assert declaration.description
        assert all("default" not in node for node in walk(declaration.parameters_json_schema))
    read_file = declarations[[d.name for d in declarations].index("read_file")]
    assert read_file.parameters_json_schema["required"] == ["path"]
    assert config.tool_config is None  # the model is never forced to call a tool
    for request in (make_request(tools=specs), make_request()):
        afc = build_config(request).automatic_function_calling
        assert afc is not None and afc.disable is True


def test_disabled_afc_takes_the_sdk_path_that_does_not_warn() -> None:
    # The SDK prints its "direct use of automatic function calling" warning only when this
    # helper says AFC is on. It defaults to on when the config doesn't disable it.
    assert _extra_utils.should_disable_afc(build_config(make_request(tools=tool_specs())))
    assert not _extra_utils.should_disable_afc(types.GenerateContentConfig(candidate_count=1))


def test_a_request_cannot_mix_tools_and_an_output_schema() -> None:
    with pytest.raises(ValidationError, match="tools or an output schema"):
        make_request(tools=tool_specs(), output_schema=BaselineOutput)


def test_tool_messages_need_a_call_id_and_tool_name() -> None:
    with pytest.raises(ValidationError):
        Message(role=Role.TOOL, text="result")


def test_function_calls_are_parsed_with_ids_and_replay_data() -> None:
    turn = signature_turn()
    result = to_completion(response(turn.parts), "m")
    assert result.stop_reason is StopReason.TOOL_USE
    assert result.text == ""  # the thought summary is not answer text
    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [
        ("call-abc", "read_file", {"path": "app/a.py", "start_line": 3}),
        ("pri-call-1", "search_code", {"pattern": "x"}),  # Gemini sent no id: we made one
    ]
    assert result.provider_data is not None
    replayed = types.Content.model_validate(result.provider_data["gemini_content"])
    assert replayed.parts == turn.parts
    assert replayed.parts is not None and replayed.parts[1].thought_signature == SIGNATURE


def test_text_turns_keep_replay_data_and_truncated_calls_are_not_tool_use() -> None:
    text = to_completion(response([types.Part(text="done")]), "m")
    assert text.stop_reason is StopReason.END and text.provider_data is not None
    call = types.Part(function_call=types.FunctionCall(name="read_file", args={"path": "a"}))
    truncated = to_completion(response([call], "MAX_TOKENS"), "m")
    assert truncated.stop_reason is StopReason.MAX_TOKENS


def test_model_turns_are_replayed_verbatim_including_signatures() -> None:
    turn = signature_turn()
    data = {"gemini_content": turn.model_dump(mode="json", exclude_none=True)}
    message = Message(role=Role.ASSISTANT, provider_data=data)
    contents = to_contents([Message.user("question"), message])
    assert contents[1] == turn
    assert contents[1].parts is not None and contents[1].parts[1].thought_signature == SIGNATURE


def test_model_turns_without_replay_data_are_rebuilt() -> None:
    calls = [
        ToolCall(id="call-abc", name="read_file", arguments={"path": "a.py"}),
        ToolCall(id="pri-call-1", name="search_code", arguments={"pattern": "x"}),
    ]
    (content,) = to_contents([Message(role=Role.ASSISTANT, text="Let me look.", tool_calls=calls)])
    assert content.role == "model" and content.parts is not None
    text, first, second = content.parts
    assert text.text == "Let me look."
    assert first.function_call is not None and first.function_call.id == "call-abc"
    assert first.function_call.args == {"path": "a.py"}
    assert second.function_call is not None and second.function_call.id is None


def test_tool_results_and_a_following_reminder_become_one_user_turn() -> None:
    calls = [
        ToolCall(id="call-abc", name="read_file", arguments={"path": "a.py"}),
        ToolCall(id="pri-call-1", name="search_code", arguments={"pattern": "("}),
    ]
    contents = to_contents(
        [
            Message.user("question"),
            Message(role=Role.ASSISTANT, tool_calls=calls),
            Message.tool_result(calls[0], "1  def f(): ..."),
            Message.tool_result(calls[1], "search failed: bad regex", is_error=True),
            Message.user("1 turn left: submit now."),
        ]
    )
    # The API rejects a "tool" role: results go back in a user turn, merged with the reminder.
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[2].parts is not None
    first, second, reminder = contents[2].parts
    assert first.function_response is not None and second.function_response is not None
    assert (first.function_response.id, first.function_response.name) == ("call-abc", "read_file")
    assert first.function_response.response == {"result": "1  def f(): ..."}
    assert second.function_response.id is None  # generated ids are never sent to Gemini
    assert second.function_response.response == {"error": "search failed: bad regex"}
    assert reminder.text == "1 turn left: submit now."


def test_a_recorded_turn_without_parts_is_replaced_by_a_placeholder() -> None:
    # A MALFORMED_FUNCTION_CALL reply can come back with empty content; Gemini rejects an
    # empty turn, but the loop still appends it to keep history complete.
    empty = Message(role=Role.ASSISTANT, provider_data={"gemini_content": {"role": "model"}})
    (content,) = to_contents([empty])
    assert content.role == "model" and content.parts is not None
    assert [p.text for p in content.parts] == ["(empty response)"]


def test_model_turns_are_never_merged() -> None:
    first = Message(role=Role.ASSISTANT, text="one")
    second = Message(role=Role.ASSISTANT, text="two")
    contents = to_contents([Message.user("q"), first, second])
    assert [c.role for c in contents] == ["user", "model", "model"]


async def test_replay_data_survives_the_response_cache(tmp_path: Any) -> None:
    completion = to_completion(response(signature_turn().parts), "m")
    cached = CachingAdapter(
        ScriptedAdapter([completion]), ResponseCache(tmp_path), CacheMode.READ_WRITE
    )
    request = make_request(tools=tool_specs())
    first = await cached.complete(request)
    second = await cached.complete(request)  # the scripted adapter is empty now: cache hit
    assert second.cached and second.provider_data == first.provider_data
    (content,) = to_contents([Message(role=Role.ASSISTANT, provider_data=second.provider_data)])
    assert content.parts is not None and content.parts[1].thought_signature == SIGNATURE


async def test_complete_sends_tools_and_returns_tool_calls() -> None:
    adapter, models = adapter_with(response(signature_turn().parts))
    result = await adapter.complete(make_request(tools=tool_specs()))
    config = models.calls[0]["config"]
    assert config.tools and config.automatic_function_calling.disable is True
    assert result.stop_reason is StopReason.TOOL_USE and len(result.tool_calls) == 2
