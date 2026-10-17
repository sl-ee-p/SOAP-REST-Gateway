"""Per-request context: request id, one access-log line, and a last-resort error handler."""

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.api.errors import problem_response
from app.logging_config import request_id_var

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller's id only if it is short and harmless (it ends up in logs and headers).
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Anything not handled by the exception handlers is a bug: log the traceback
                # with the request id, and give the client a generic 500 without internals.
                logger.exception("unhandled_error")
                response = problem_response(
                    request, 500, "internal_error", "An unexpected error occurred."
                )
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client": request.client.host if request.client else None,
                },
            )
            return response
        finally:
            request_id_var.reset(token)
