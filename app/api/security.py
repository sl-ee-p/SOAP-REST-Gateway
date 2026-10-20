"""API-key authentication and per-key rate limiting, as FastAPI dependencies."""

import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

# auto_error=False: we raise our own 401 so it uses the same problem+json format as the rest.
api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="API key issued to the client"
)


def key_id_for(api_key: str) -> str:
    """A short, non-reversible label for logs and rate-limit buckets. Never log the key."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:12]


def _is_valid(candidate: str, valid_keys: list[str]) -> bool:
    # compare_digest takes the same time wherever the strings differ, so response timing
    # doesn't leak how much of a guess was right. Every key is checked (no early exit) for the
    # same reason.
    matches = [hmac.compare_digest(candidate.encode(), key.encode()) for key in valid_keys]
    return any(matches)


def require_api_key(
    request: Request, api_key: Annotated[str | None, Depends(api_key_header)]
) -> str:
    """Return the key id of the authenticated caller, or raise 401."""
    if not api_key or not _is_valid(api_key, request.app.state.settings.api_keys):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key. Send it in the X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    key_id = key_id_for(api_key)
    request.state.key_id = key_id  # picked up by the access log
    return key_id


def enforce_rate_limit(request: Request, key_id: Annotated[str, Depends(require_api_key)]) -> None:
    result = request.app.state.rate_limiter.check(key_id)
    headers = {
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(result.remaining),
    }
    # The middleware adds these to whatever response this request ends with. A 404, 422 or
    # 502 costs a token just like a 200, so the client must see its budget on those too
    # (headers set on a dependency's `Response` only reach successful responses).
    request.state.rate_limit_headers = headers
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Retry after the number of seconds in Retry-After.",
            headers={**headers, "Retry-After": str(result.retry_after_seconds)},
        )
