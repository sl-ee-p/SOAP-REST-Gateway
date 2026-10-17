"""Health probes, used by Docker/Kubernetes and load balancers. Not versioned, no auth."""

from fastapi import APIRouter, Request, Response

from app import __version__
from app.api.schemas import Health, Readiness
from app.domain.errors import UpstreamError

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="Liveness: the process is up")
def liveness() -> Health:
    # Deliberately does not call upstream: an upstream outage must not get this
    # container restarted, since restarting would not fix it.
    return Health(version=__version__)


@router.get(
    "/ready",
    summary="Readiness: the upstream SOAP service answers",
    responses={503: {"model": Readiness, "description": "Upstream unavailable"}},
)
def readiness(request: Request, response: Response) -> Readiness:
    try:
        request.app.state.health_gateway.list_continents()
    except UpstreamError as exc:
        response.status_code = 503
        return Readiness(status="unavailable", upstream=exc.code)
    return Readiness(status="ready", upstream="ok")
