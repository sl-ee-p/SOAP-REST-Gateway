from app.infra.cache import InMemoryTTLCache
from tests.fakes import FakeClock


def test_returns_none_for_unknown_key():
    assert InMemoryTTLCache(ttl_seconds=10).get("missing") is None


def test_returns_stored_value_before_expiry(clock: FakeClock):
    cache = InMemoryTTLCache(ttl_seconds=10, clock=clock)
    cache.set("k", "v")
    clock.advance(9.9)
    assert cache.get("k") == "v"


def test_entry_expires_after_ttl(clock: FakeClock):
    cache = InMemoryTTLCache(ttl_seconds=10, clock=clock)
    cache.set("k", "v")
    clock.advance(10)
    assert cache.get("k") is None


def test_ttl_zero_disables_caching(clock: FakeClock):
    cache = InMemoryTTLCache(ttl_seconds=0, clock=clock)
    cache.set("k", "v")
    assert cache.get("k") is None


def test_falsy_values_are_cached_distinctly_from_missing(clock: FakeClock):
    # An empty list is a legitimate cached answer; only None means "not cached".
    cache = InMemoryTTLCache(ttl_seconds=10, clock=clock)
    cache.set("k", [])
    assert cache.get("k") == []


def test_full_cache_evicts_expired_entries_first(clock: FakeClock):
    cache = InMemoryTTLCache(ttl_seconds=10, max_entries=2, clock=clock)
    cache.set("old", 1)
    clock.advance(5)
    cache.set("newer", 2)
    clock.advance(6)  # "old" has expired, "newer" has not
    cache.set("newest", 3)
    assert cache.get("old") is None
    assert cache.get("newer") == 2
    assert cache.get("newest") == 3


def test_full_cache_evicts_oldest_when_nothing_expired(clock: FakeClock):
    cache = InMemoryTTLCache(ttl_seconds=100, max_entries=2, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") is None
    assert (cache.get("b"), cache.get("c")) == (2, 3)


def test_overwriting_a_key_does_not_evict(clock: FakeClock):
    cache = InMemoryTTLCache(ttl_seconds=100, max_entries=2, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("a", 10)
    assert (cache.get("a"), cache.get("b")) == (10, 2)


def test_clear_removes_everything(clock: FakeClock):
    cache = InMemoryTTLCache(ttl_seconds=10, clock=clock)
    cache.set("k", "v")
    cache.clear()
    assert cache.get("k") is None
