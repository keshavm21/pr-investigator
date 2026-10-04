"""Offline adapters for tests and smoke runs. Neither calls a network service.

- ScriptedAdapter returns pre-written responses in order (unit and integration tests).
- FakeAdapter answers any structured request with a minimal valid object (CLI smoke runs).
"""

import json
from collections.abc import Callable, Iterable
from typing import Any

from pr_investigator.llm.types import Capabilities, Completion, LLMRequest, StopReason

_OFFLINE = Capabilities(
    structured_output=True, tool_calling=True, may_train_on_inputs=False, paid=False
)

ScriptItem = Completion | str | dict[str, Any]
Script = ScriptItem | Callable[[LLMRequest], ScriptItem]


class ScriptedAdapter:
    provider = "fake"

    def __init__(self, script: Iterable[Script], *, model: str = "scripted") -> None:
        self.model = model
        self.capabilities = _OFFLINE
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> Completion:
        self.requests.append(request)
        if not self._script:
            raise AssertionError(f"ScriptedAdapter ran out of responses at stage {request.stage!r}")
        item = self._script.pop(0)
        if callable(item):
            item = item(request)
        return self._completion(item)

    def _completion(self, item: ScriptItem) -> Completion:
        if isinstance(item, Completion):
            return item
        text = item if isinstance(item, str) else json.dumps(item)
        return Completion(
            provider=self.provider, model=self.model, text=text, stop_reason=StopReason.END
        )


class FakeAdapter:
    """Returns the smallest object that satisfies the requested schema (empty lists, etc.)."""

    provider = "fake"

    def __init__(self, model: str = "minimal") -> None:
        self.model = model
        self.capabilities = _OFFLINE

    async def complete(self, request: LLMRequest) -> Completion:
        text = "fake response"
        if request.output_schema is not None:
            schema = request.output_schema.model_json_schema()
            text = json.dumps(minimal_instance(schema, schema.get("$defs", {})))
        return Completion(
            provider=self.provider, model=self.model, text=text, stop_reason=StopReason.END
        )


def minimal_instance(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    """Build the smallest JSON value matching a (pydantic-generated) JSON schema."""
    if "$ref" in schema:
        return minimal_instance(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        options = schema["anyOf"]
        if any(option.get("type") == "null" for option in options):
            return None
        return minimal_instance(options[0], defs)
    kind = schema.get("type")
    if kind == "object":
        return {
            name: minimal_instance(prop, defs)
            for name, prop in schema.get("properties", {}).items()
            if name in schema.get("required", [])
        }
    if kind == "array":
        return []
    if kind == "string":
        return "(fake)"
    if kind in ("integer", "number"):
        return schema.get("minimum", 1)
    if kind == "boolean":
        return False
    return None
