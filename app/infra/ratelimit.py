"""Per-key rate limiting with a token bucket.

Each key owns a bucket holding up to `burst` tokens that refills at `per_minute / 60` tokens
per second. A request spends one token; with none left it is refused. This allows short
bursts while enforcing the average rate.
"""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int  # bucket size, reported as X-RateLimit-Limit
    remaining: int  # whole tokens left after this request
    retry_after_seconds: int  # when refused: seconds until a token is available


class RateLimiter(Protocol):
    def check(self, key: str) -> RateLimitResult: ...


class TokenBucketRateLimiter:
    def __init__(
        self,
        per_minute: int,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._burst = burst
        self._refill_per_second = per_minute / 60
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_update)
        self._lock = threading.Lock()

    def check(self, key: str) -> RateLimitResult:
        with self._lock:
            now = self._clock()
            tokens, last = self._buckets.get(key, (float(self._burst), now))
            tokens = min(self._burst, tokens + (now - last) * self._refill_per_second)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return RateLimitResult(True, self._burst, int(tokens - 1), 0)
            self._buckets[key] = (tokens, now)
            wait = (1 - tokens) / self._refill_per_second
            return RateLimitResult(False, self._burst, 0, max(1, math.ceil(wait)))
