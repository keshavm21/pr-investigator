"""Build the configured LLM client. Configuration, not routing: exactly one provider per run.

Stack (outermost first):
  LLMClient       data policy, structured-output validation and repair
  CachingAdapter  record/replay and response cache (hits cost nothing)
  GuardedAdapter  rate limit, request cap, retries on 429/5xx (real calls only)
  provider        GeminiAdapter, or FakeAdapter for offline runs
"""

from pr_investigator.config import Settings
from pr_investigator.errors import ConfigError, PaidProviderBlockedError
from pr_investigator.llm.cache import CachingAdapter, ResponseCache
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.fake import FakeAdapter
from pr_investigator.llm.gemini import GeminiAdapter
from pr_investigator.llm.guards import GuardedAdapter, RateLimiter, RequestBudget
from pr_investigator.llm.types import ProviderAdapter


def build_provider(settings: Settings) -> ProviderAdapter:
    if settings.llm_provider == "fake":
        return FakeAdapter()
    if settings.gemini_api_key is None:
        raise ConfigError(
            "GEMINI_API_KEY is not set. Put it in .env (gitignored) or export it in your shell; "
            "see .env.example."
        )
    return GeminiAdapter(
        model=settings.llm_model,
        api_key=settings.gemini_api_key.get_secret_value(),
        tier=settings.gemini_tier,
    )


def build_client(
    settings: Settings,
    *,
    provider: ProviderAdapter | None = None,
    budget: RequestBudget | None = None,
) -> LLMClient:
    provider = provider if provider is not None else build_provider(settings)
    if provider.capabilities.paid and not settings.allow_paid_providers:
        raise PaidProviderBlockedError(
            f"{provider.provider} is configured as a paid provider, but paid providers are "
            "disabled (PRI_ALLOW_PAID_PROVIDERS=false)."
        )
    guarded = GuardedAdapter(
        provider,
        rate_limiter=RateLimiter(settings.llm_requests_per_minute),
        budget=budget if budget is not None else RequestBudget(),
        max_retries=settings.llm_max_retries,
    )
    cached = CachingAdapter(guarded, ResponseCache(settings.cache_dir), settings.llm_cache_mode)
    return LLMClient(cached)
