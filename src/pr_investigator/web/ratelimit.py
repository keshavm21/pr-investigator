"""A small in-process sliding-window rate limiter for the sign-in and webhook endpoints.

It keys on the direct peer address. Behind a reverse proxy that is the proxy's address, so the
limit then applies to all clients together; the limits below are set with that in mind.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Record a request and say whether it is within the limit."""
        now = self._clock()
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        if len(self._hits) > 10_000:  # forget idle keys, so memory stays bounded
            for stale in [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window]:
                del self._hits[stale]
        return True
