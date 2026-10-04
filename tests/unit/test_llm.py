"""LLMClient, response cache, guards and factory. All offline."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from pr_investigator.config import CacheMode, Settings
from pr_investigator.domain import DataClass
from pr_investigator.errors import ConfigError, DataPolicyError, PaidProviderBlockedError
from pr_investigator.llm.cache import CachingAdapter, ResponseCache, request_key
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.factory import build_client
from pr_investigator.llm.fake import FakeAdapter, ScriptedAdapter, minimal_instance
from pr_investigator.llm.guards import GuardedAdapter, RateLimiter, RequestBudget
from pr_investigator.llm.types import (
    CacheMissError,
    Capabilities,
    Completion,
    LLMOutputError,
    LLMQuotaExhaustedError,
    LLMRateLimitError,
    LLMRefusedError,
    LLMRequest,
    LLMTransientError,
    Message,
    RequestBudgetExceededError,
    Role,
    StopReason,
    Usage,
)
from pr_investigator.review.baseline import BaselineOutput


class Answer(BaseModel):
    value: int


def request(**overrides: object) -> LLMRequest:
    fields: dict[str, object] = {
        "stage": "test",
        "data_class": DataClass.TEST,
        "system": "system prompt",
        "messages": [Message.user("question")],
        "output_schema": Answer,
    }
    fields.update(overrides)
    return LLMRequest(**fields)  # type: ignore[arg-type]


def completion(text: str, stop: StopReason = StopReason.END, tokens: int = 10) -> Completion:
    return Completion(
        provider="fake",
        model="scripted",
        text=text,
        stop_reason=stop,
        usage=Usage(input_tokens=tokens, output_tokens=tokens),
    )


class TrainingProvider(ScriptedAdapter):
    """A scripted provider whose terms allow training on inputs (like a free tier)."""

    def __init__(self) -> None:
        super().__init__(['{"value": 1}'])
        self.capabilities = Capabilities(
            structured_output=True, tool_calling=False, may_train_on_inputs=True, paid=False
        )


# --- LLMClient -------------------------------------------------------------------------------


async def test_valid_structured_output_is_parsed() -> None:
    client = LLMClient(ScriptedAdapter(['{"value": 42}']))
    response = await client.generate(request())
    assert isinstance(response.parsed, Answer) and response.parsed.value == 42
    assert response.attempts == 1


async def test_invalid_output_gets_one_appended_repair_attempt() -> None:
    adapter = ScriptedAdapter([completion('{"value": "nope"}'), completion('{"value": 7}')])
    response = await LLMClient(adapter).generate(request())
    assert isinstance(response.parsed, Answer) and response.parsed.value == 7
    assert response.attempts == 2 and response.usage.input_tokens == 20
    repair = adapter.requests[1].messages
    assert [m.role for m in repair] == [Role.USER, Role.ASSISTANT, Role.USER]
    assert repair[1].text == '{"value": "nope"}'  # history is appended to, never edited
    assert "value" in repair[2].text


async def test_second_invalid_output_raises() -> None:
    adapter = ScriptedAdapter(["not json", "still not json"])
    with pytest.raises(LLMOutputError):
        await LLMClient(adapter).generate(request())


async def test_truncated_output_is_not_repaired() -> None:
    adapter = ScriptedAdapter([completion('{"val', StopReason.MAX_TOKENS)])
    with pytest.raises(LLMOutputError, match="cut off"):
        await LLMClient(adapter).generate(request())
    assert len(adapter.requests) == 1


async def test_refusal_raises_refused_error() -> None:
    adapter = ScriptedAdapter([completion("", StopReason.REFUSED)])
    with pytest.raises(LLMRefusedError):
        await LLMClient(adapter).generate(request())


async def test_code_fences_are_tolerated() -> None:
    client = LLMClient(ScriptedAdapter(['```json\n{"value": 3}\n```']))
    response = await client.generate(request())
    assert isinstance(response.parsed, Answer) and response.parsed.value == 3


async def test_private_data_never_reaches_a_provider_that_may_train_on_it() -> None:
    provider = TrainingProvider()
    client = LLMClient(provider)
    with pytest.raises(DataPolicyError):
        await client.generate(request(data_class=DataClass.PRIVATE))
    assert provider.requests == []
    response = await client.generate(request(data_class=DataClass.PUBLIC))
    assert isinstance(response.parsed, Answer)


# --- cache -----------------------------------------------------------------------------------


def test_cache_key_depends_on_everything_that_shapes_the_response() -> None:
    base = request_key("p", "m", request())
    assert base == request_key("p", "m", request())
    assert base != request_key("p", "m", request(sample=1))
    assert base != request_key("p", "other-model", request())
    assert base != request_key("p", "m", request(output_schema=BaselineOutput))
    assert base != request_key("p", "m", request(messages=[Message.user("different")]))


async def test_read_write_cache_serves_repeats_without_calling_the_provider(
    tmp_path: Path,
) -> None:
    inner = ScriptedAdapter(['{"value": 5}'])
    cached = CachingAdapter(inner, ResponseCache(tmp_path), CacheMode.READ_WRITE)
    first = await LLMClient(cached).generate(request())
    second = await LLMClient(cached).generate(request())
    assert len(inner.requests) == 1
    assert not first.cached and second.cached
    assert second.parsed == first.parsed
    stored = next(tmp_path.rglob("*.json"))
    assert json.loads(stored.read_text())["request"]["stage"] == "test"


async def test_replay_mode_fails_on_a_miss_and_errors_are_not_cached(tmp_path: Path) -> None:
    replay = CachingAdapter(ScriptedAdapter([]), ResponseCache(tmp_path), CacheMode.REPLAY)
    with pytest.raises(CacheMissError):
        await replay.complete(request())
    inner = ScriptedAdapter([completion("", StopReason.ERROR), completion('{"value": 1}')])
    writer = CachingAdapter(inner, ResponseCache(tmp_path), CacheMode.READ_WRITE)
    await writer.complete(request())
    await writer.complete(request())
    assert len(inner.requests) == 2  # the error response wasn't served from cache


# --- guards ----------------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_rate_limiter_waits_for_the_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(2, clock=clock, sleep=clock.sleep)
    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()
    assert clock.sleeps == [60.0]


class FlakyAdapter(ScriptedAdapter):
    def __init__(self, failures: list[Exception]) -> None:
        super().__init__(['{"value": 1}'])
        self.failures = failures

    async def complete(self, req: LLMRequest) -> Completion:
        if self.failures:
            self.requests.append(req)
            raise self.failures.pop(0)
        return await super().complete(req)


def guarded(adapter: ScriptedAdapter, clock: FakeClock, budget: RequestBudget) -> GuardedAdapter:
    return GuardedAdapter(
        adapter,
        rate_limiter=RateLimiter(100, clock=clock, sleep=clock.sleep),
        budget=budget,
        max_retries=2,
        sleep=clock.sleep,
    )


async def test_rate_limit_errors_are_retried_with_the_suggested_delay() -> None:
    clock = FakeClock()
    budget = RequestBudget()
    adapter = FlakyAdapter([LLMRateLimitError("429", retry_after=31), LLMTransientError("503")])
    result = await guarded(adapter, clock, budget).complete(request())
    assert result.text == '{"value": 1}'
    assert clock.sleeps == [31, 20.0]  # suggested delay, then backoff for attempt 2
    assert budget.used == 3  # retries are real requests


async def test_persistent_rate_limiting_reports_exhausted_quota() -> None:
    clock = FakeClock()
    adapter = FlakyAdapter([LLMRateLimitError("429")] * 3)
    with pytest.raises(LLMQuotaExhaustedError):
        await guarded(adapter, clock, RequestBudget()).complete(request())


async def test_request_budget_stops_calls() -> None:
    clock = FakeClock()
    adapter = ScriptedAdapter(['{"value": 1}', '{"value": 2}'])
    wrapped = guarded(adapter, clock, RequestBudget(1))
    await wrapped.complete(request())
    with pytest.raises(RequestBudgetExceededError):
        await wrapped.complete(request())


# --- factory and fake provider ---------------------------------------------------------------


def test_factory_requires_an_api_key_for_gemini(settings: Settings) -> None:
    gemini = settings.model_copy(update={"llm_provider": "gemini"})
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        build_client(gemini)


def test_factory_blocks_paid_providers_by_default(settings: Settings) -> None:
    paid = ScriptedAdapter([])
    paid.capabilities = Capabilities(
        structured_output=True, tool_calling=True, may_train_on_inputs=False, paid=True
    )
    with pytest.raises(PaidProviderBlockedError):
        build_client(settings, provider=paid)
    allowed = settings.model_copy(update={"allow_paid_providers": True})
    assert build_client(allowed, provider=paid).provider == "fake"


async def test_fake_provider_returns_schema_valid_objects() -> None:
    response = await LLMClient(FakeAdapter()).generate(request(output_schema=BaselineOutput))
    assert isinstance(response.parsed, BaselineOutput) and response.parsed.findings == []
    schema = BaselineOutput.model_json_schema()
    BaselineOutput.model_validate(minimal_instance(schema, schema["$defs"]))
