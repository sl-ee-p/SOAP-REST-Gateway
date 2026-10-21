import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.domain.errors import NotFound, UpstreamTimeout
from app.infra.cache import InMemoryTTLCache
from app.infra.cached_gateway import CachedCountryInfoGateway, CacheTrace, cache_trace_var
from tests.fakes import FakeClock, FakeGateway, make_country


@pytest.fixture
def cached(gateway: FakeGateway, clock: FakeClock) -> CachedCountryInfoGateway:
    return CachedCountryInfoGateway(gateway, InMemoryTTLCache(ttl_seconds=60, clock=clock))


@pytest.fixture
def trace():
    t = CacheTrace()
    token = cache_trace_var.set(t)
    yield t
    cache_trace_var.reset(token)


def test_second_call_is_served_from_cache(cached, gateway, trace):
    cached.list_countries()
    cached.list_countries()
    assert gateway.calls["list_countries"] == 1
    assert (trace.misses, trace.hits) == (1, 1)
    # A request that needed any upstream call reports MISS, even if part of it was cached.
    assert trace.status == "MISS"


def test_request_served_only_from_cache_reports_hit(cached, trace):
    cached.list_countries()
    fresh = CacheTrace()
    token = cache_trace_var.set(fresh)
    try:
        cached.list_countries()
    finally:
        cache_trace_var.reset(token)
    assert fresh.status == "HIT"


def test_first_call_is_a_miss(cached, trace):
    cached.list_continents()
    assert trace.status == "MISS"


def test_cache_expires_with_the_ttl(cached, gateway, clock):
    cached.list_currencies()
    clock.advance(61)
    cached.list_currencies()
    assert gateway.calls["list_currencies"] == 2


def test_bypass_always_calls_upstream_and_refreshes_the_entry(cached, gateway, trace):
    cached.list_countries()
    trace.bypass = True
    cached.list_countries()
    assert gateway.calls["list_countries"] == 2
    assert trace.status == "BYPASS"
    trace.bypass = False
    cached.list_countries()  # served from the entry the bypass just wrote
    assert gateway.calls["list_countries"] == 2


def test_country_lookups_are_cached_per_code_ignoring_case(cached, gateway):
    cached.get_country("it")
    cached.get_country("IT")
    cached.get_country("FR")
    assert gateway.calls["get_country"] == 2


def test_not_found_is_cached_briefly_then_fetched_again(cached, gateway, clock, trace):
    with pytest.raises(NotFound) as first:
        cached.get_country("ZZ")
    with pytest.raises(NotFound) as second:
        cached.get_country("ZZ")
    assert gateway.calls["get_country"] == 1
    assert second.value.detail == first.value.detail == "Country 'ZZ' was not found"
    assert (trace.misses, trace.hits) == (1, 1)
    clock.advance(61)  # past the 60 s negative TTL, though within the normal 300 s one
    with pytest.raises(NotFound):
        cached.get_country("ZZ")
    assert gateway.calls["get_country"] == 2


def test_not_found_ttl_zero_disables_the_negative_cache(gateway, clock):
    cached = CachedCountryInfoGateway(
        gateway, InMemoryTTLCache(ttl_seconds=60, clock=clock), not_found_ttl_seconds=0
    )
    for _ in range(2):
        with pytest.raises(NotFound):
            cached.get_country("ZZ")
    assert gateway.calls["get_country"] == 2


def test_bypass_still_calls_upstream_for_a_cached_not_found(cached, gateway, trace):
    with pytest.raises(NotFound):
        cached.get_country("ZZ")
    trace.bypass = True
    with pytest.raises(NotFound):
        cached.get_country("ZZ")
    assert gateway.calls["get_country"] == 2


def test_a_country_that_appears_replaces_the_cached_not_found(cached, gateway, trace):
    with pytest.raises(NotFound):
        cached.get_country("ZZ")
    gateway.countries.append(make_country("ZZ", "Zedland", "EU", "EUR"))
    trace.bypass = True
    assert cached.get_country("ZZ").code == "ZZ"
    trace.bypass = False
    assert cached.get_country("ZZ").code == "ZZ"


def test_upstream_failure_is_not_cached_and_recovers(cached, gateway):
    gateway.error = UpstreamTimeout("slow")
    with pytest.raises(UpstreamTimeout):
        cached.list_countries()
    with pytest.raises(UpstreamTimeout):
        cached.list_countries()
    assert gateway.calls["list_countries"] == 2
    gateway.error = None
    assert cached.list_countries()


def test_empty_results_are_cached_too(cached, gateway):
    cached.countries_using_currency("XXX")
    cached.countries_using_currency("XXX")
    assert gateway.calls["countries_using_currency"] == 1


def test_callers_cannot_modify_the_cached_list(cached):
    cached.list_countries().clear()
    assert cached.list_countries()


def test_works_without_a_request_trace(cached):
    assert cache_trace_var.get() is None
    assert cached.list_continents()


def test_trace_status_is_none_when_cache_unused():
    assert CacheTrace().status is None


# --- concurrent misses ("single flight") -----------------------------------------------------


def run_concurrently(gateway: FakeGateway, call, threads: int = 5) -> list:
    """Run `call` in several threads while the upstream method is held, then release it.

    The upstream call blocks until every thread has had time to miss the cache, so without
    coalescing each thread would reach upstream. Returns each thread's result or exception.
    """
    release = threading.Event()
    original = gateway.list_countries

    def slow_list_countries():
        release.wait(5)
        return original()

    gateway.list_countries = slow_list_countries
    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(call) for _ in range(threads)]
        time.sleep(0.2)  # let every thread reach the cache before upstream answers
        release.set()
        return [f.exception() or f.result() for f in futures]


def test_concurrent_misses_share_one_upstream_call(cached, gateway):
    results = run_concurrently(gateway, cached.list_countries)
    assert gateway.calls["list_countries"] == 1
    assert all(result == results[0] and result for result in results)


def test_concurrent_waiters_get_the_leaders_error(cached, gateway):
    gateway.error = UpstreamTimeout("slow")
    results = run_concurrently(gateway, cached.list_countries)
    assert all(isinstance(result, UpstreamTimeout) for result in results)


def test_a_failed_fetch_does_not_block_the_next_one(cached, gateway):
    gateway.error = UpstreamTimeout("slow")
    with pytest.raises(UpstreamTimeout):
        cached.list_countries()
    gateway.error = None
    assert cached.list_countries()
    assert gateway.calls["list_countries"] == 2


def test_waiters_get_their_own_copy_of_the_list(cached, gateway):
    first, second = run_concurrently(gateway, cached.list_countries, threads=2)
    assert first == second and first is not second
