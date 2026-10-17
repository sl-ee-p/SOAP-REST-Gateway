"""Application factory: builds the FastAPI app and wires every layer together.

Run with:  uvicorn app.main:create_app --factory
"""

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import RedirectResponse

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.routes import continents, countries, currencies, health
from app.api.schemas import problem_responses
from app.api.security import enforce_rate_limit
from app.config import Settings, get_settings
from app.domain.ports import CountryInfoGateway
from app.domain.service import CountryService
from app.infra.cache import Cache, InMemoryTTLCache
from app.infra.cached_gateway import CachedCountryInfoGateway
from app.infra.ratelimit import RateLimiter, TokenBucketRateLimiter
from app.logging_config import configure_logging
from app.soap.gateway import build_gateway

DESCRIPTION = """
A clean REST/JSON API in front of the public
[CountryInfoService](http://webservices.oorsprong.org/websamples.countryinfo/CountryInfoService.wso)
SOAP web service.

Errors use [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) `application/problem+json`
bodies with a stable `code` field. Every response carries an `X-Request-ID` header.

**Authentication:** send your key in the `X-API-Key` header (all `/v1` routes). Health
probes are public. **Rate limit:** per key; see the `X-RateLimit-*` headers and `Retry-After`
on 429. **Caching:** upstream data is cached; `X-Cache` is `HIT`, `MISS` or `BYPASS`. Send
`Cache-Control: no-cache` to force fresh data.
"""

TAGS = [
    {"name": "countries", "description": "Countries, with filtering and pagination."},
    {"name": "continents", "description": "The continents used to group countries."},
    {"name": "currencies", "description": "Currencies and the countries using them."},
    {"name": "health", "description": "Liveness and readiness probes."},
]


def create_app(
    settings: Settings | None = None,
    gateway: CountryInfoGateway | None = None,
    cache: Cache | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own settings, a fake gateway, cache or rate limiter."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if not settings.api_keys:
        # Fail fast: running with no keys would mean either an open API or one nobody can use.
        raise RuntimeError("API_KEYS is not set. Configure at least one key (see .env.example).")

    app = FastAPI(
        title="SOAP → REST Gateway",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
    )
    app.state.settings = settings
    app.state.cache = cache or InMemoryTTLCache(
        settings.cache_ttl_seconds, max_entries=settings.cache_max_entries
    )
    app.state.rate_limiter = rate_limiter or TokenBucketRateLimiter(
        settings.rate_limit_per_minute, settings.rate_limit_burst
    )
    app.state.service = CountryService(
        CachedCountryInfoGateway(gateway or build_gateway(settings), app.state.cache)
    )
    app.state.health_gateway = gateway or build_gateway(
        settings, timeout_seconds=settings.health_check_timeout_seconds, max_retries=0
    )

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    # Every /v1 route requires a valid API key and counts against that key's rate limit.
    v1 = APIRouter(
        prefix="/v1",
        dependencies=[Depends(enforce_rate_limit)],
        responses=problem_responses(401, 429),
    )
    v1.include_router(countries.router)
    v1.include_router(continents.router)
    v1.include_router(currencies.router)
    app.include_router(v1)
    app.include_router(health.router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    return app
