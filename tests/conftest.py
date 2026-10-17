import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infra.cache import InMemoryTTLCache
from app.infra.ratelimit import TokenBucketRateLimiter
from app.main import create_app
from tests.fakes import FakeClock, FakeGateway

API_KEY = "test-key-0123456789abcdef"
OTHER_API_KEY = "other-key-0123456789abcdef"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def settings() -> Settings:
    # Explicit values (not environment or .env) keep the tests independent of the machine.
    return Settings(
        _env_file=None,
        api_keys=[API_KEY, OTHER_API_KEY],
        cache_ttl_seconds=60,
        rate_limit_per_minute=60,
        rate_limit_burst=3,
        log_level="CRITICAL",
    )


@pytest.fixture
def client(settings: Settings, gateway: FakeGateway, clock: FakeClock) -> TestClient:
    app = create_app(
        settings,
        gateway=gateway,
        cache=InMemoryTTLCache(settings.cache_ttl_seconds, clock=clock),
        rate_limiter=TokenBucketRateLimiter(
            settings.rate_limit_per_minute, settings.rate_limit_burst, clock=clock
        ),
    )
    # raise_server_exceptions=False: behave like a real server and return the 500 response.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}
