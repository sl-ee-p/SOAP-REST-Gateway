"""Application factory: builds the FastAPI app and wires every layer together.

Run with:  uvicorn app.main:create_app --factory
"""

from fastapi import APIRouter, FastAPI
from fastapi.responses import RedirectResponse

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.routes import continents, countries, currencies, health
from app.config import Settings, get_settings
from app.domain.ports import CountryInfoGateway
from app.domain.service import CountryService
from app.logging_config import configure_logging
from app.soap.gateway import build_gateway

DESCRIPTION = """
A clean REST/JSON API in front of the public
[CountryInfoService](http://webservices.oorsprong.org/websamples.countryinfo/CountryInfoService.wso)
SOAP web service.

Errors use [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) `application/problem+json`
bodies with a stable `code` field. Every response carries an `X-Request-ID` header.
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
) -> FastAPI:
    """Build the app. Tests pass their own settings and a fake gateway."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="SOAP → REST Gateway",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
    )
    app.state.settings = settings
    app.state.service = CountryService(gateway or build_gateway(settings))
    app.state.health_gateway = gateway or build_gateway(
        settings, timeout_seconds=settings.health_check_timeout_seconds, max_retries=0
    )

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    v1 = APIRouter(prefix="/v1")
    v1.include_router(countries.router)
    v1.include_router(continents.router)
    v1.include_router(currencies.router)
    app.include_router(v1)
    app.include_router(health.router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    return app
