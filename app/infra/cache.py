"""Response cache: a small interface plus an in-memory TTL implementation.

The service depends on the `Cache` Protocol, so a Redis-backed class with the same three
methods could replace this one without touching the service.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any) -> None: ...

    def clear(self) -> None: ...


class InMemoryTTLCache:
    """Thread-safe dict whose entries expire `ttl_seconds` after they were stored.

    Per-process only: with several workers or replicas each one has its own cache.
    `max_entries` bounds memory; when full, expired entries go first, then the least
    recently used one.
    """

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock  # injectable so tests don't need to sleep
        # Ordered from least to most recently used: reads and writes move a key to the end.
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if self._clock() >= expires_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        if self._ttl <= 0:  # TTL 0 disables caching
            return
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            elif len(self._data) >= self._max_entries:
                self._evict()
            self._data[key] = (self._clock() + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def _evict(self) -> None:
        now = self._clock()
        for key in [k for k, (expires_at, _) in self._data.items() if expires_at <= now]:
            del self._data[key]
        if len(self._data) >= self._max_entries:
            self._data.popitem(last=False)  # the least recently used entry
