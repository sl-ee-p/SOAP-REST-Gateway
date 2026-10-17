"""Domain errors.

Lower layers translate technical failures (SOAP faults, socket timeouts, ...) into these,
and the API layer translates these into HTTP responses. Neither side needs to know about
the other: the SOAP code never mentions status codes, the routes never mention zeep.
Each error has a stable machine-readable `code` that clients can rely on.
"""


class DomainError(Exception):
    code = "internal_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFound(DomainError):
    code = "not_found"


class UpstreamError(DomainError):
    """Base class for every failure of the upstream SOAP service."""

    code = "upstream_error"


class UpstreamFault(UpstreamError):
    """The service answered with a SOAP Fault."""

    code = "upstream_fault"


class UpstreamBadResponse(UpstreamError):
    """The service answered, but with an HTTP error or a response we could not parse."""

    code = "upstream_bad_response"


class UpstreamTimeout(UpstreamError):
    """The service did not answer in time (after all retries)."""

    code = "upstream_timeout"


class UpstreamUnavailable(UpstreamError):
    """The service could not be reached at all (DNS, connection refused, ...)."""

    code = "upstream_unavailable"
