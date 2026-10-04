"""Record/replay and response caching: one mechanism, three modes (see config.CacheMode).

Responses are stored as JSON files keyed by a hash of everything that determines the response:
provider, model and the full request (including `sample`, so repeated eval samples of the same
prompt are distinct entries). Re-running an eval replays its responses instead of spending quota.
"""

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pr_investigator.config import CacheMode
from pr_investigator.llm.types import (
    CacheMissError,
    Capabilities,
    Completion,
    LLMRequest,
    ProviderAdapter,
    StopReason,
)

_CACHEABLE = (StopReason.END, StopReason.TOOL_USE, StopReason.MAX_TOKENS, StopReason.REFUSED)


def request_key(provider: str, model: str, request: LLMRequest) -> str:
    payload = {"provider": provider, "model": model, "request": request.cache_payload()}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ResponseCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> Completion | None:
        path = self.path_for(key)
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        return Completion.model_validate(record["completion"])

    def put(self, key: str, request: LLMRequest, completion: Completion) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "key": key,
            "recorded_at": datetime.now(UTC).isoformat(),
            "request": request.cache_payload(),
            "completion": completion.model_dump(mode="json"),
        }
        # Write atomically so an interrupted run never leaves a half-written entry.
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


class CachingAdapter:
    """Wraps an adapter with the response cache."""

    def __init__(self, inner: ProviderAdapter, cache: ResponseCache, mode: CacheMode) -> None:
        self.inner = inner
        self.cache = cache
        self.mode = mode
        self.hits = 0
        self.misses = 0

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
        if self.mode is CacheMode.OFF:
            return await self.inner.complete(request)
        key = request_key(self.provider, self.model, request)
        cached = self.cache.get(key)
        if cached is not None:
            self.hits += 1
            return cached.model_copy(update={"cached": True})
        self.misses += 1
        if self.mode is CacheMode.REPLAY:
            raise CacheMissError(
                f"no recorded response for stage {request.stage!r} (key {key[:12]}); "
                "record one with PRI_LLM_CACHE_MODE=read_write"
            )
        completion = await self.inner.complete(request)
        if completion.stop_reason in _CACHEABLE:
            self.cache.put(key, request, completion)
        return completion
