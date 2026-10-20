"""Cache-aside decorator for the CountryInfoGateway port.

It wraps any gateway and caches the results of upstream calls. Caching at this level (raw
upstream data) means every filter/page combination of /v1/countries shares one cached
`FullCountryInfoAllCountries` result, and the service needs no cache code at all.

Concurrent misses on the same key are coalesced: one request fetches, the others wait for
its result instead of each calling upstream ("single flight").
"""

import threading
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from app.domain.models import Continent, Country, CountrySummary, Currency
from app.domain.ports import CountryInfoGateway
from app.infra.cache import Cache

T = TypeVar("T")


@dataclass
class CacheTrace:
    """Per-request record: did the client ask to bypass, and what happened.

    The middleware creates one per request and reads it back to build the X-Cache header.
    It is a mutable object because routes run in a worker thread: changes made to a
    ContextVar *inside* that thread are not visible outside it, but mutations of a shared
    object are.
    """

    bypass: bool = False
    hits: int = 0
    misses: int = 0

    @property
    def status(self) -> str | None:
        if self.bypass:
            return "BYPASS"
        if self.misses:
            return "MISS"
        if self.hits:
            return "HIT"
        return None  # the request did not touch the cache


cache_trace_var: ContextVar[CacheTrace | None] = ContextVar("cache_trace", default=None)


@dataclass
class _Flight:
    """One upstream fetch in progress, which other requests for the same key wait for."""

    done: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: BaseException | None = None


class CachedCountryInfoGateway:
    def __init__(self, inner: CountryInfoGateway, cache: Cache) -> None:
        self._inner = inner
        self._cache = cache
        self._flights: dict[str, _Flight] = {}
        self._flights_lock = threading.Lock()

    def get_country(self, code: str) -> Country:
        return self._cached(
            f"country:{code.strip().upper()}", lambda: self._inner.get_country(code)
        )

    def list_countries(self) -> list[Country]:
        return self._cached("countries", self._inner.list_countries)

    def list_continents(self) -> list[Continent]:
        return self._cached("continents", self._inner.list_continents)

    def list_currencies(self) -> list[Currency]:
        return self._cached("currencies", self._inner.list_currencies)

    def countries_using_currency(self, currency_code: str) -> list[CountrySummary]:
        key = f"currency-countries:{currency_code.strip().upper()}"
        return self._cached(key, lambda: self._inner.countries_using_currency(currency_code))

    def _cached(self, key: str, fetch: Callable[[], T]) -> T:
        trace = cache_trace_var.get()
        if trace is not None and trace.bypass:
            # Skip the read, but still refresh the entry so the next normal request is fresh.
            value = fetch()
            self._cache.set(key, value)
            return _copy(value)
        cached = self._cache.get(key)
        if cached is not None:
            if trace is not None:
                trace.hits += 1
            return _copy(cached)
        if trace is not None:
            trace.misses += 1
        return _copy(self._fetch_once(key, fetch))

    def _fetch_once(self, key: str, fetch: Callable[[], T]) -> T:
        """Fetch and store `key`, unless another thread is already doing so: then wait for it.

        Without this, a cold or just-expired key under load sends one upstream call per
        waiting request (a "thundering herd"). Followers get the leader's result, or its
        error: if upstream just failed, calling it again at once would most likely fail too,
        and every follower would wait for its own full retry cycle.
        """
        with self._flights_lock:
            flight = self._flights.get(key)
            is_leader = flight is None
            if flight is None:
                flight = self._flights[key] = _Flight()
        if not is_leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return cast(T, flight.value)
        try:
            value = fetch()  # errors propagate and are never cached
            self._cache.set(key, value)
            flight.value = value
            return value
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with self._flights_lock:
                del self._flights[key]
            flight.done.set()


def _copy(value: T) -> T:
    # Models are frozen, but lists are not: hand out a copy so a caller can't alter the cache.
    return cast(T, list(value)) if isinstance(value, list) else value
