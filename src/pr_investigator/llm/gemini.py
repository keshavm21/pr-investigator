"""Gemini adapter, using the official Google Gen AI SDK (`google-genai`).

Phase 1 supports text and structured (JSON-schema) output. Tool calling is added in Phase 2.
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
        if request.tools:
            raise LLMRequestError("tool calling for Gemini is not implemented yet (Phase 2)")
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=[_to_content(m) for m in request.messages],
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
    }
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


def _to_content(message: Message) -> types.Content:
    if message.role is Role.TOOL or message.tool_calls:
        raise LLMRequestError("tool messages for Gemini are not implemented yet (Phase 2)")
    role = "user" if message.role is Role.USER else "model"
    # Gemini rejects empty parts; an empty earlier response still has to be representable.
    return types.Content(role=role, parts=[types.Part(text=message.text or "(empty response)")])


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
    parts = candidate.content.parts if candidate.content and candidate.content.parts else []
    text = "".join(p.text for p in parts if p.text and not p.thought)
    reason = _name(candidate.finish_reason) if candidate.finish_reason is not None else None
    if reason == "STOP" or (reason is None and text):
        stop, detail = StopReason.END, None
    elif reason == "MAX_TOKENS":
        stop, detail = StopReason.MAX_TOKENS, None
    elif reason in _REFUSAL_REASONS:
        stop, detail = StopReason.REFUSED, reason
    else:
        stop, detail = StopReason.ERROR, f"finish reason {reason}"
    return Completion(
        provider="gemini",
        model=model,
        text=text,
        stop_reason=stop,
        stop_detail=detail,
        usage=usage,
        raw=raw,
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
        return LLMRateLimitError(
            f"Gemini rate limit or quota hit: {exc.message}", retry_after=_retry_delay(exc)
        )
    return LLMRequestError(f"Gemini rejected the request ({exc.code}): {exc.message}")


def _retry_delay(exc: genai_errors.APIError) -> float | None:
    """Read google.rpc.RetryInfo's retryDelay (for example "31s") from the error, if present."""
    blob = json.dumps(getattr(exc, "details", None), default=str)
    match = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', blob)
    return float(match.group(1)) if match else None
