"""Turn exceptions into consistent `application/problem+json` responses.

This is the single place where domain errors meet HTTP status codes.
"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import PROBLEM_MEDIA_TYPE
from app.domain.errors import (
    DomainError,
    NotFound,
    UpstreamBadResponse,
    UpstreamError,
    UpstreamFault,
    UpstreamTimeout,
    UpstreamUnavailable,
)

logger = logging.getLogger(__name__)

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFound: 404,
    UpstreamFault: 502,  # Bad Gateway: upstream answered, but with an error
    UpstreamBadResponse: 502,
    UpstreamUnavailable: 503,  # Service Unavailable: could not reach upstream
    UpstreamTimeout: 504,  # Gateway Timeout: upstream too slow
}

# Upstream details (operation names, fault text) are logged, not sent to clients:
# they describe our internals and could change at any time.
PUBLIC_UPSTREAM_DETAIL: dict[str, str] = {
    UpstreamFault.code: "The upstream service rejected the request.",
    UpstreamBadResponse.code: "The upstream service returned an invalid response.",
    UpstreamUnavailable.code: "The upstream service is unavailable. Please retry later.",
    UpstreamTimeout.code: "The upstream service did not respond in time. Please retry later.",
}


def problem_response(
    request: Request,
    status: int,
    code: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
        "request_id": getattr(request.state, "request_id", None),
        **extra,
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


def _status_for(exc: DomainError) -> int:
    for error_type in type(exc).__mro__:
        if error_type in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[error_type]
    return 500


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)  # noqa: S101 - narrows the type; registered for it
    if isinstance(exc, UpstreamError):
        detail = PUBLIC_UPSTREAM_DETAIL.get(exc.code, "The upstream service failed.")
    else:
        detail = exc.detail
    return problem_response(request, _status_for(exc), exc.code, detail)


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Keep only stable, serialisable fields (pydantic may include raw exceptions in `ctx`).
    errors = [
        {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in exc.errors()
    ]
    return problem_response(
        request, 422, "validation_error", "The request is invalid.", errors=errors
    )


async def _http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    return problem_response(
        request, exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None)
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
