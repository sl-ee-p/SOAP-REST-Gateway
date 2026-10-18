from app.infra.ratelimit import TokenBucketRateLimiter
from tests.fakes import FakeClock


def test_allows_a_burst_then_refuses(clock: FakeClock):
    limiter = TokenBucketRateLimiter(per_minute=60, burst=3, clock=clock)
    results = [limiter.check("a") for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]
    assert all(r.limit == 3 for r in results)


def test_refusal_reports_when_to_retry(clock: FakeClock):
    limiter = TokenBucketRateLimiter(per_minute=30, burst=1, clock=clock)  # 1 token / 2s
    limiter.check("a")
    refused = limiter.check("a")
    assert not refused.allowed
    assert refused.retry_after_seconds >= 2


def test_tokens_refill_over_time(clock: FakeClock):
    limiter = TokenBucketRateLimiter(per_minute=60, burst=1, clock=clock)  # 1 token / second
    assert limiter.check("a").allowed
    assert not limiter.check("a").allowed
    clock.advance(1)
    assert limiter.check("a").allowed


def test_bucket_never_exceeds_burst(clock: FakeClock):
    limiter = TokenBucketRateLimiter(per_minute=60, burst=2, clock=clock)
    clock.advance(3600)  # a long idle period must not bank thousands of tokens
    assert [limiter.check("a").allowed for _ in range(3)] == [True, True, False]


def test_keys_have_independent_buckets(clock: FakeClock):
    limiter = TokenBucketRateLimiter(per_minute=60, burst=1, clock=clock)
    assert limiter.check("a").allowed
    assert not limiter.check("a").allowed
    assert limiter.check("b").allowed


def test_retry_after_is_the_exact_ceiling_of_the_wait(clock: FakeClock):
    limiter = TokenBucketRateLimiter(per_minute=30, burst=1, clock=clock)  # 1 token / 2s
    limiter.check("a")
    assert limiter.check("a").retry_after_seconds == 2  # not 3: the token is back after 2s
    clock.advance(0.5)
    assert limiter.check("a").retry_after_seconds == 2  # 1.5s left, rounded up
