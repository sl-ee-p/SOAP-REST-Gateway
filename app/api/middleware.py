"""Per-request context: request id, one access-log line, and a last-resort error handler."""

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.api.errors import problem_response
from app.infra.cached_gateway import CacheTrace, cache_trace_var
from app.logging_config import request_id_var

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "X-Request-ID"
CACHE_HEADER = "X-Cache"
# Accept a caller's id only if it is short and harmless (it ends up in logs and headers).
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


def choose_request_id(incoming: str) -> str:
    """Reuse the caller's id if it is safe, otherwise generate a new one."""
    # fullmatch, not match with `$`: `$` also matches just before a trailing newline.
    return incoming if _SAFE_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = choose_request_id(request.headers.get(REQUEST_ID_HEADER, ""))
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        # `no-cache` is the standard way for a client to say "give me fresh data".
        trace = CacheTrace(bypass="no-cache" in request.headers.get("cache-control", "").lower())
        trace_token = cache_trace_var.set(trace)
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
            if trace.status:
                response.headers[CACHE_HEADER] = trace.status
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client": request.client.host if request.client else None,
                    "key_id": getattr(request.state, "key_id", None),
                    "cache": trace.status,
                },
            )
            return response
        finally:
            request_id_var.reset(token)
            cache_trace_var.reset(trace_token)
