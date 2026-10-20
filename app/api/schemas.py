"""API-only schemas: error bodies and health responses.

Resource bodies (Country, Page, ...) reuse the domain models directly; see DECISIONS.md.
"""

from http import HTTPStatus
from typing import Any, Literal

from pydantic import BaseModel, Field

PROBLEM_MEDIA_TYPE = "application/problem+json"


class FieldError(BaseModel):
    """One invalid input value, in the `errors` list of a 422 problem."""

    loc: list[str | int] = Field(
        description="Where the value is: its location, then its name", examples=[["query", "limit"]]
    )
    msg: str = Field(examples=["Input should be less than or equal to 250"])
    type: str = Field(description="Stable, machine-readable reason", examples=["less_than_equal"])


class Problem(BaseModel):
    """Error body following RFC 9457 ("Problem Details for HTTP APIs")."""

    type: str = Field(default="about:blank", examples=["about:blank"])
    title: str = Field(examples=["Not Found"])
    status: int = Field(examples=[404])
    detail: str = Field(examples=["Country 'ZZ' was not found"])
    instance: str = Field(description="Path of the request", examples=["/v1/countries/ZZ"])
    code: str = Field(description="Stable, machine-readable error code", examples=["not_found"])
    request_id: str | None = Field(default=None, examples=["9f1c0a7e2b8d4c55a3e1f0b6c2d4e8a1"])
    errors: list[FieldError] | None = Field(
        default=None, description="Per-field validation errors (422 only)"
    )


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses=` entries documenting the possible error statuses of a route."""
    return {
        status: {
            "model": Problem,
            "description": HTTPStatus(status).phrase,
            "content": {PROBLEM_MEDIA_TYPE: {}},
        }
        for status in statuses
    }


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class Readiness(BaseModel):
    status: Literal["ready", "unavailable"]
    upstream: str = Field(description="`ok`, or the error code of the failed upstream check")
