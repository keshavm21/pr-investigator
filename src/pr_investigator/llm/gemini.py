"""Gemini adapter, using the official Google Gen AI SDK (`google-genai`).

Supports text, structured (JSON-schema) output and tool calling. The harness runs tools itself:
the SDK's automatic function calling is always disabled, and the model is never forced to call a
tool. Each model turn is kept verbatim (`provider_data`) and replayed unchanged, because Google
requires thought blocks and their signatures to be resent exactly as received.
"""

import json
import re
from typing import Any, Literal

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from pr_investigator.llm.types import (
    Capabilities,
    Completion,
    Effort,
    LLMError,
    LLMRateLimitError,
    LLMRequest,
    LLMRequestError,
    LLMTransientError,
    Message,
    Role,
    StopReason,
    ToolCall,
    Usage,
)

# JSON-schema keywords Gemini's structured output accepts. Everything else (such as the
# "default" values pydantic emits) is stripped before sending.
_SCHEMA_KEYWORDS = {
    "type",
    "properties",
    "required",
    "items",
    "prefixItems",
    "minItems",
    "maxItems",
    "enum",
    "anyOf",
    "$ref",
    "$defs",
    "description",
    "title",
    "format",
    "minimum",
    "maximum",
    "additionalProperties",
}

_REFUSAL_REASONS = {
    "SAFETY",
    "PROHIBITED_CONTENT",
    "BLOCKLIST",
    "SPII",
    "RECITATION",
    "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT",
    "IMAGE_RECITATION",
}

_THINKING_LEVELS = {Effort.LOW: types.ThinkingLevel.LOW, Effort.HIGH: types.ThinkingLevel.HIGH}

# Ids we generate when Gemini returns a function call without one. Gemini never sees them.
_GENERATED_ID_PREFIX = "pri-call-"
# Key under which provider_data holds Gemini's raw record of a model turn.
_CONTENT_KEY = "gemini_content"


class GeminiAdapter:
    provider = "gemini"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        tier: Literal["free", "paid"] = "free",
        client: Any = None,
    ) -> None:
        self.model = model
        self.capabilities = Capabilities(
            structured_output=True,
            tool_calling=True,
            parallel_tool_calls=True,
            prompt_caching=False,
            may_train_on_inputs=tier == "free",
            paid=tier == "paid",
        )
        self._client = client if client is not None else genai.Client(api_key=api_key)

    async def complete(self, request: LLMRequest) -> Completion:
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=to_contents(request.messages),
                config=build_config(request),
            )
        except genai_errors.ClientError as exc:
            raise _client_error(exc) from exc
        except genai_errors.ServerError as exc:
            raise LLMTransientError(f"Gemini server error ({exc.code}): {exc.message}") from exc
        except genai_errors.APIError as exc:
            raise LLMError(f"Gemini API error ({exc.code}): {exc.message}") from exc
        except (httpx.TimeoutException, httpx.TransportError, TimeoutError) as exc:
            raise LLMTransientError(f"network error calling Gemini: {exc!r}") from exc
        return to_completion(response, self.model)


def build_config(request: LLMRequest) -> types.GenerateContentConfig:
    kwargs: dict[str, Any] = {
        "system_instruction": request.system,
        "max_output_tokens": request.max_output_tokens,
        "candidate_count": 1,
        # The harness runs tools itself. Disabling the SDK's automatic function calling keeps it
        # from acting on tool calls, and also stops its warning about calling generate_content.
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }
    if request.tools:
        # No tool_config: the model decides whether to call a tool (never forced).
        kwargs["tools"] = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=spec.name,
                        description=spec.description,
                        parameters_json_schema=_clean_schema(spec.parameters),
                    )
                    for spec in request.tools
                ]
            )
        ]
    if request.output_schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_json_schema"] = gemini_schema(request.output_schema)
    # Medium (or no hint) keeps the model's default thinking level.
    level = _THINKING_LEVELS.get(request.effort) if request.effort is not None else None
    if level is not None:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=level)
    return types.GenerateContentConfig(**kwargs)


def gemini_schema(model: type[BaseModel]) -> dict[str, Any]:
    cleaned = _clean_schema(model.model_json_schema())
    assert isinstance(cleaned, dict)
    return cleaned


def _clean_schema(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _SCHEMA_KEYWORDS:
            continue
        if key in ("properties", "$defs"):
            # Keys here are field or definition names, not keywords: keep them all.
            out[key] = {name: _clean_schema(sub) for name, sub in value.items()}
        else:
            out[key] = _clean_schema(value)
    return out


def to_contents(messages: list[Message]) -> list[types.Content]:
    """Convert the conversation into Gemini turns.

    Tool results go back as `function_response` parts in a `user` turn; the API rejects a
    `tool` role. Consecutive user-side messages (tool results, then a reminder from the harness)
    are merged into one turn. Model turns are never merged, so each is replayed exactly as
    received.
    """
    contents: list[types.Content] = []
    pending: list[types.Part] = []  # parts of the user turn being built
    for message in messages:
        if message.role is Role.ASSISTANT:
            if pending:
                contents.append(types.Content(role="user", parts=pending))
                pending = []
            contents.append(_model_content(message))
        elif message.role is Role.TOOL:
            pending.append(_function_response(message))
        else:
            pending.append(types.Part(text=message.text or "(empty message)"))
    if pending:
        contents.append(types.Content(role="user", parts=pending))
    return contents


def _model_content(message: Message) -> types.Content:
    raw = (message.provider_data or {}).get(_CONTENT_KEY)
    if raw is not None:
        # Gemini's own record of the turn, including thought signatures, resent unchanged.
        content = types.Content.model_validate(raw)
        if content.parts:
            return content
    parts: list[types.Part] = []
    if message.text:
        parts.append(types.Part(text=message.text))
    for call in message.tool_calls:
        parts.append(
            types.Part(
                function_call=types.FunctionCall(
                    id=_provider_id(call.id), name=call.name, args=call.arguments
                )
            )
        )
    # Gemini rejects empty parts; an empty earlier response still has to be representable.
    return types.Content(role="model", parts=parts or [types.Part(text="(empty response)")])


def _function_response(message: Message) -> types.Part:
    assert message.tool_name is not None  # guaranteed by Message validation
    payload = {"error": message.text} if message.is_error else {"result": message.text}
    return types.Part(
        function_response=types.FunctionResponse(
            id=_provider_id(message.tool_call_id), name=message.tool_name, response=payload
        )
    )


def _provider_id(call_id: str | None) -> str | None:
    """Gemini's own call id, or None for ids we generated (Gemini never issued those)."""
    if call_id is None or call_id.startswith(_GENERATED_ID_PREFIX):
        return None
    return call_id


def to_completion(response: types.GenerateContentResponse, model: str) -> Completion:
    raw = response.model_dump(mode="json", exclude_none=True)
    usage = _usage(response.usage_metadata)
    feedback = response.prompt_feedback
    if feedback is not None and feedback.block_reason is not None:
        return Completion(
            provider="gemini",
            model=model,
            stop_reason=StopReason.REFUSED,
            stop_detail=f"prompt blocked: {_name(feedback.block_reason)}",
            usage=usage,
            raw=raw,
        )
    candidates = response.candidates or []
    if not candidates:
        return Completion(
            provider="gemini",
            model=model,
            stop_reason=StopReason.ERROR,
            stop_detail="no candidates returned",
            usage=usage,
            raw=raw,
        )
    candidate = candidates[0]
    content = candidate.content
    parts = content.parts if content and content.parts else []
    text = "".join(p.text for p in parts if p.text and not p.thought)
    calls = [p.function_call for p in parts if p.function_call is not None]
    tool_calls = [
        ToolCall(
            id=call.id or f"{_GENERATED_ID_PREFIX}{index}",
            name=call.name or "",
            arguments=dict(call.args or {}),
        )
        for index, call in enumerate(calls)
    ]
    reason = _name(candidate.finish_reason) if candidate.finish_reason is not None else None
    if reason == "MAX_TOKENS":
        stop, detail = StopReason.MAX_TOKENS, None
    elif reason in _REFUSAL_REASONS:
        stop, detail = StopReason.REFUSED, reason
    elif tool_calls and reason in ("STOP", None):
        stop, detail = StopReason.TOOL_USE, None
    elif reason == "STOP" or (reason is None and text):
        stop, detail = StopReason.END, None
    else:
        stop, detail = StopReason.ERROR, f"finish reason {reason}"
    return Completion(
        provider="gemini",
        model=model,
        text=text,
        tool_calls=tool_calls,
        stop_reason=stop,
        stop_detail=detail,
        usage=usage,
        raw=raw,
        provider_data=(
            {_CONTENT_KEY: content.model_dump(mode="json", exclude_none=True)} if content else None
        ),
    )


def _usage(meta: types.GenerateContentResponseUsageMetadata | None) -> Usage:
    if meta is None:
        return Usage()
    return Usage(
        input_tokens=meta.prompt_token_count or 0,
        output_tokens=meta.candidates_token_count or 0,
        thinking_tokens=meta.thoughts_token_count or 0,
        cached_input_tokens=meta.cached_content_token_count or 0,
    )


def _name(value: Any) -> str:
    return str(getattr(value, "value", value))


def _client_error(exc: genai_errors.ClientError) -> LLMError:
    if exc.code == 429:
        quotas = _quota_ids(exc)
        which = f" ({', '.join(quotas)})" if quotas else ""
        return LLMRateLimitError(
            f"Gemini rate limit or quota hit{which}: {exc.message}", retry_after=_retry_delay(exc)
        )
    return LLMRequestError(f"Gemini rejected the request ({exc.code}): {exc.message}")


def _quota_ids(exc: genai_errors.APIError) -> list[str]:
    """Which quotas were exceeded, from google.rpc.QuotaFailure (for example a per-day limit)."""
    blob = json.dumps(getattr(exc, "details", None), default=str)
    return list(dict.fromkeys(re.findall(r'"quotaId":\s*"([^"]+)"', blob)))


def _retry_delay(exc: genai_errors.APIError) -> float | None:
    """Read google.rpc.RetryInfo's retryDelay (for example "31s") from the error, if present."""
    blob = json.dumps(getattr(exc, "details", None), default=str)
    match = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', blob)
    return float(match.group(1)) if match else None
