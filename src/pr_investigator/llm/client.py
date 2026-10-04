"""LLMClient: the only LLM entry point the pipeline uses.

It adds three provider-independent guarantees on top of an adapter:
1. Data policy: content the provider may not see never reaches it.
2. Structured output comes back validated, or the call fails with LLMOutputError.
   An invalid response gets exactly one repair attempt, appended to the conversation.
3. Usage is summed across attempts.
"""

import json

from pydantic import BaseModel, ValidationError

from pr_investigator.domain import DataClass
from pr_investigator.errors import DataPolicyError
from pr_investigator.llm.types import (
    Capabilities,
    Completion,
    LLMOutputError,
    LLMRefusedError,
    LLMRequest,
    LLMResponse,
    Message,
    ProviderAdapter,
    StopReason,
)

_REPAIR_PROMPT = (
    "Your previous response could not be used: {error}\n"
    "Reply again with only a JSON object that matches the required schema."
)


def ensure_data_allowed(data_class: DataClass, capabilities: Capabilities, provider: str) -> None:
    if data_class is DataClass.PRIVATE and capabilities.may_train_on_inputs:
        raise DataPolicyError(
            f"refusing to send private repository content to {provider}: its current terms "
            "may use inputs for training (for example, Gemini's free tier). Use a public "
            "repository or test data, or configure a provider that doesn't train on inputs."
        )


class LLMClient:
    def __init__(self, adapter: ProviderAdapter) -> None:
        self.adapter = adapter

    @property
    def provider(self) -> str:
        return self.adapter.provider

    @property
    def model(self) -> str:
        return self.adapter.model

    @property
    def capabilities(self) -> Capabilities:
        return self.adapter.capabilities

    async def generate(self, request: LLMRequest) -> LLMResponse:
        ensure_data_allowed(request.data_class, self.capabilities, self.provider)
        completion = await self.adapter.complete(request)
        if request.output_schema is None:
            return _response(completion, [completion], parsed=None)

        attempts = [completion]
        parsed, error = _try_parse(completion, request.output_schema)
        if parsed is None and _repairable(completion):
            repair = request.model_copy(
                update={
                    "messages": [
                        *request.messages,
                        Message.assistant(completion.text),
                        Message.user(_REPAIR_PROMPT.format(error=error)),
                    ]
                }
            )
            completion = await self.adapter.complete(repair)
            attempts.append(completion)
            parsed, error = _try_parse(completion, request.output_schema)
        if parsed is None:
            if completion.stop_reason is StopReason.REFUSED:
                raise LLMRefusedError(
                    f"{self.provider} declined the {request.stage} request"
                    + (f": {completion.stop_detail}" if completion.stop_detail else "")
                )
            raise LLMOutputError(f"{request.stage}: {error}")
        return _response(completion, attempts, parsed=parsed)


def _repairable(completion: Completion) -> bool:
    # Refusals, errors and truncation won't be fixed by asking again.
    return completion.stop_reason is StopReason.END


def _try_parse(
    completion: Completion, schema: type[BaseModel]
) -> tuple[BaseModel | None, str | None]:
    if completion.stop_reason is StopReason.MAX_TOKENS:
        return None, "the response was cut off at the output token limit"
    if completion.stop_reason is not StopReason.END:
        return None, f"the response ended with stop reason {completion.stop_reason.value!r}"
    text = _strip_code_fence(completion.text)
    if not text:
        return None, "the response was empty"
    try:
        return schema.model_validate_json(text), None
    except ValidationError as exc:
        return None, _summarize_validation_error(exc)
    except json.JSONDecodeError as exc:  # pragma: no cover - pydantic wraps these
        return None, f"invalid JSON: {exc}"


def _strip_code_fence(text: str) -> str:
    """Some models wrap JSON in ```json fences even when asked not to."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _summarize_validation_error(exc: ValidationError) -> str:
    problems = [
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in exc.errors()[:5]
    ]
    return "the JSON didn't match the schema (" + "; ".join(problems) + ")"


def _response(
    final: Completion, attempts: list[Completion], parsed: BaseModel | None
) -> LLMResponse:
    usage = attempts[0].usage
    for extra in attempts[1:]:
        usage = usage + extra.usage
    return LLMResponse(
        provider=final.provider,
        model=final.model,
        text=final.text,
        parsed=parsed,
        tool_calls=final.tool_calls,
        stop_reason=final.stop_reason,
        usage=usage,
        attempts=len(attempts),
        cached=all(c.cached for c in attempts),
    )
