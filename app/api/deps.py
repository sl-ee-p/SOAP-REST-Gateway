"""FastAPI dependencies shared by the routes."""

from typing import Annotated

from fastapi import Depends, Query, Request

from app.domain.service import CountryService

MAX_PAGE_SIZE = 250


def get_service(request: Request) -> CountryService:
    return request.app.state.service


ServiceDep = Annotated[CountryService, Depends(get_service)]

Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description=f"Page size (1-{MAX_PAGE_SIZE})")]
Offset = Annotated[int, Query(ge=0, description="Number of items to skip")]
