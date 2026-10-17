from fastapi import APIRouter

from app.api.deps import Limit, Offset, ServiceDep
from app.api.schemas import problem_responses
from app.domain.models import Continent, Page

router = APIRouter(prefix="/continents", tags=["continents"])


@router.get(
    "",
    summary="List continents",
    responses=problem_responses(422, 502, 503, 504),
)
def list_continents(service: ServiceDep, limit: Limit = 50, offset: Offset = 0) -> Page[Continent]:
    return service.list_continents(limit=limit, offset=offset)
