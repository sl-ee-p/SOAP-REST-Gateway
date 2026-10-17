from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.api.deps import Limit, Offset, ServiceDep
from app.api.schemas import problem_responses
from app.domain.models import Country, Page

router = APIRouter(prefix="/countries", tags=["countries"])

UPSTREAM_ERRORS = (502, 503, 504)


@router.get(
    "",
    summary="List countries",
    description="Countries sorted by name, with optional filters. Filters are case-insensitive.",
    responses=problem_responses(422, *UPSTREAM_ERRORS),
)
def list_countries(
    service: ServiceDep,
    limit: Limit = 50,
    offset: Offset = 0,
    continent: Annotated[
        str | None,
        Query(pattern=r"^[A-Za-z]{2}$", description="Continent code", examples=["EU"]),
    ] = None,
    currency: Annotated[
        str | None,
        Query(pattern=r"^[A-Za-z]{3}$", description="ISO 4217 currency code", examples=["EUR"]),
    ] = None,
    q: Annotated[
        str | None,
        Query(min_length=1, max_length=100, description="Name contains", examples=["land"]),
    ] = None,
) -> Page[Country]:
    return service.list_countries(
        limit=limit, offset=offset, continent=continent, currency=currency, name_contains=q
    )


@router.get(
    "/{code}",
    summary="Get one country",
    responses=problem_responses(404, 422, *UPSTREAM_ERRORS),
)
def get_country(
    service: ServiceDep,
    code: Annotated[
        str,
        Path(pattern=r"^[A-Za-z]{2}$", description="ISO 3166-1 alpha-2 code", examples=["IT"]),
    ],
) -> Country:
    return service.get_country(code)
