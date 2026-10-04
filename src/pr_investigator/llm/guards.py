"""Client-side protection for real provider calls: rate limiting, a request cap, and retries.

These wrap the provider adapter *below* the cache, so cache hits cost nothing and aren't counted.
"""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

from pr_investigator.llm.types import (
    Capabilities,
    Completion,
    LLMQuotaExhaustedError,
    LLMRateLimitError,
    LLMRequest,
    LLMTransientError,
    ProviderAdapter,
    RequestBudgetExceededError,
)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class RateLimiter:
    """At most `requests_per_minute` calls in any 60-second window."""

    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._calls and now - self._calls[0] >= 60.0:
                    self._calls.popleft()
                if len(self._calls) < self.requests_per_minute:
                    self._calls.append(now)
                    return
                await self._sleep(60.0 - (now - self._calls[0]))


class RequestBudget:
    """Caps real provider requests per run (including retries). None means unlimited."""

    def __init__(self, max_requests: int | None = None) -> None:
        self.max_requests = max_requests
        self.used = 0

    def take(self) -> None:
        if self.max_requests is not None and self.used >= self.max_requests:
            raise RequestBudgetExceededError(
                f"request cap of {self.max_requests} reached; raise --max-requests to continue"
            )
        self.used += 1


class GuardedAdapter:
    def __init__(
        self,
        inner: ProviderAdapter,
        *,
        rate_limiter: RateLimiter,
        budget: RequestBudget,
        max_retries: int = 3,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.inner = inner
        self.rate_limiter = rate_limiter
        self.budget = budget
        self.max_retries = max_retries
        self._sleep = sleep

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def capabilities(self) -> Capabilities:
        return self.inner.capabilities

    async def complete(self, request: LLMRequest) -> Completion:
        attempt = 0
        while True:
            self.budget.take()
            await self.rate_limiter.acquire()
            try:
                return await self.inner.complete(request)
            except LLMRateLimitError as exc:
                if attempt >= self.max_retries:
                    raise LLMQuotaExhaustedError(
                        f"{self.provider} kept rate-limiting after {attempt + 1} attempts. "
                        "The free quota may be used up for now: try again later, lower "
                        "PRI_LLM_REQUESTS_PER_MINUTE, or switch PRI_LLM_MODEL."
                    ) from exc
                delay = exc.retry_after if exc.retry_after is not None else _backoff(attempt)
            except LLMTransientError:
                if attempt >= self.max_retries:
                    raise
                delay = _backoff(attempt)
            await self._sleep(delay)
            attempt += 1


def _backoff(attempt: int) -> float:
    return float(min(60, 10 * 2**attempt))
