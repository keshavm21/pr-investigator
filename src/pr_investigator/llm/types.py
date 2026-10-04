"""Provider-neutral request and response types.

The review pipeline only ever builds `LLMRequest`s and reads `LLMResponse`s. Adapters translate
these to and from a specific provider's API.
"""

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from pr_investigator.domain import DataClass
from pr_investigator.errors import PRIError


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Effort(StrEnum):
    """A hint for how much reasoning to spend. Adapters map it to the provider or ignore it."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StopReason(StrEnum):
    END = "end"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSED = "refused"
    ERROR = "error"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(description="JSON schema of the tool's arguments")


class Message(BaseModel):
    role: Role
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # set on TOOL messages: which call this result answers

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role=Role.USER, text=text)

    @classmethod
    def assistant(cls, text: str) -> "Message":
        return cls(role=Role.ASSISTANT, text=text)


class LLMRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    stage: str = Field(description="Pipeline stage, for traces and per-stage configuration")
    data_class: DataClass = Field(description="Sensitivity of the content in this request")
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    output_schema: type[BaseModel] | None = None
    max_output_tokens: int = 16_384
    effort: Effort | None = None
    sample: int = Field(
        default=0, description="Distinguishes repeated samples of an identical request in the cache"
    )

    def cache_payload(self) -> dict[str, Any]:
        """Everything that determines the response, in JSON-serializable form."""
        return {
            "stage": self.stage,
            "system": self.system,
            "messages": [m.model_dump(mode="json") for m in self.messages],
            "tools": [t.model_dump(mode="json") for t in self.tools],
            "output_schema": (
                self.output_schema.model_json_schema() if self.output_schema is not None else None
            ),
            "max_output_tokens": self.max_output_tokens,
            "effort": self.effort,
            "sample": self.sample,
        }


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cached_input_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            thinking_tokens=self.thinking_tokens + other.thinking_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
        )


class Completion(BaseModel):
    """One provider response, before any validation."""

    provider: str
    model: str
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason
    stop_detail: str | None = None
    usage: Usage = Field(default_factory=Usage)
    raw: dict[str, Any] = Field(default_factory=dict)
    cached: bool = False


class LLMResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    model: str
    text: str
    parsed: BaseModel | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason
    usage: Usage
    attempts: int = 1
    cached: bool = False


class Capabilities(BaseModel):
    structured_output: bool
    tool_calling: bool
    parallel_tool_calls: bool = False
    prompt_caching: bool = False
    context_window: int | None = None
    may_train_on_inputs: bool = Field(description="Provider terms allow training on inputs")
    paid: bool = Field(description="Calls cost money")


class ProviderAdapter(Protocol):
    """What every provider integration implements: one request in, one completion out."""

    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> Capabilities: ...

    async def complete(self, request: LLMRequest) -> Completion: ...


class LLMError(PRIError):
    """Base class for LLM failures."""


class LLMRequestError(LLMError):
    """The provider rejected the request (bad model name, invalid config, ...). Not retryable."""


class LLMRateLimitError(LLMError):
    """The provider rate-limited the request. Retryable after `retry_after` seconds."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMTransientError(LLMError):
    """A server-side or network failure that may succeed on retry."""


class LLMQuotaExhaustedError(LLMError):
    """Still rate-limited after all retries; the free quota is probably used up for now."""


class RequestBudgetExceededError(LLMError):
    """This run hit its cap on real provider requests."""


class CacheMissError(LLMError):
    """Replay mode found no recorded response for a request."""


class LLMOutputError(LLMError):
    """The model's output couldn't be used (invalid structured output, truncated, ...)."""


class LLMRefusedError(LLMError):
    """The provider declined to answer (safety filter)."""
