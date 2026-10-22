from pr_investigator.web.ratelimit import RateLimiter


def test_limits_per_key_and_recovers_after_the_window() -> None:
    now = [0.0]
    limiter = RateLimiter(2, 60, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")  # another client is unaffected
    now[0] = 61.0
    assert limiter.allow("a")
