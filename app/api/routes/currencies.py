from typing import Annotated

from fastapi import APIRouter, Path

from app.api.deps import Limit, Offset, ServiceDep
from app.api.schemas import problem_responses
from app.domain.models import CountrySummary, Currency, Page

router = APIRouter(prefix="/currencies", tags=["currencies"])

CurrencyCode = Annotated[
    str, Path(pattern=r"^[A-Za-z]{3}$", description="ISO 4217 code", examples=["EUR"])
]


@router.get(
    "",
    summary="List currencies",
    responses=problem_responses(422, 502, 503, 504),
)
def list_currencies(service: ServiceDep, limit: Limit = 50, offset: Offset = 0) -> Page[Currency]:
    return service.list_currencies(limit=limit, offset=offset)


@router.get(
    "/{code}/countries",
    summary="List countries using a currency",
    responses=problem_responses(404, 422, 502, 503, 504),
)
def countries_using_currency(
    service: ServiceDep, code: CurrencyCode, limit: Limit = 50, offset: Offset = 0
) -> Page[CountrySummary]:
    return service.countries_using_currency(code, limit=limit, offset=offset)
