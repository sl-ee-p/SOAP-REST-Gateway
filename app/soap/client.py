"""Construction of the zeep SOAP client.

Only this module knows how zeep is configured: which WSDL file, which endpoint, which timeout.
"""

from contextvars import ContextVar
from pathlib import Path
from typing import Any

import requests
import zeep
from requests.adapters import HTTPAdapter
from zeep.proxy import ServiceProxy
from zeep.transports import Transport

# Binding name as declared in the WSDL (SOAP 1.1 variant).
BINDING = "{http://www.oorsprong.org/websamples.countryinfo}CountryInfoServiceSoapBinding"

# Upstream responses are untrusted XML. SOAP forbids a DOCTYPE in messages (SOAP 1.1, 3), so
# reject DTDs and entity declarations outright (no XXE, no "billion laughs"), never fetch
# external resources while parsing, and keep libxml2's size limits on (xml_huge_tree=False).
# These are set explicitly rather than relying on zeep's defaults, which allow DTDs and
# external fetches.
XML_SETTINGS = zeep.Settings(
    strict=True,
    forbid_dtd=True,
    forbid_entities=True,
    forbid_external=True,
    xml_huge_tree=False,
)

# Every worker thread shares one session. FastAPI runs sync routes in a pool of 40 threads,
# while requests keeps only 10 connections per host by default: under load the other calls
# would open a new connection each time and throw it away afterwards (urllib3 logs a
# "Connection pool is full" warning for every one).
HTTP_POOL_SIZE = 40

# Upper bound for the timeout of the next HTTP request, set by the gateway from what is left
# of its overall deadline. A ContextVar, because zeep's Transport.operation_timeout is shared
# by all worker threads and must not be changed per call.
MIN_ATTEMPT_TIMEOUT = 0.1
timeout_cap_var: ContextVar[float | None] = ContextVar("soap_timeout_cap", default=None)


class CappedTimeoutSession(requests.Session):
    """A session that never waits longer than the current call's remaining time budget."""

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:
        cap = timeout_cap_var.get()
        timeout = kwargs.get("timeout")
        if cap is not None and timeout is not None:
            # requests also accepts a (connect, read) tuple; zeep passes a single number.
            kwargs["timeout"] = max(min(timeout, cap), MIN_ATTEMPT_TIMEOUT)
        return super().request(*args, **kwargs)


def build_service(
    wsdl_path: Path,
    endpoint_url: str,
    timeout_seconds: float,
    session: requests.Session | None = None,
) -> ServiceProxy:
    """Return a service proxy whose methods are the SOAP operations.

    The WSDL is read from a local file, so starting the app never needs the network. The
    endpoint address is set from configuration instead of trusting the URL inside the WSDL.
    """
    if session is None:
        session = CappedTimeoutSession()
        adapter = HTTPAdapter(pool_maxsize=HTTP_POOL_SIZE)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    transport = Transport(
        session=session,
        # Applies to each SOAP call (connect and read separately), not to loading the WSDL.
        operation_timeout=timeout_seconds,
    )
    client = zeep.Client(wsdl=str(wsdl_path), transport=transport, settings=XML_SETTINGS)
    return client.create_service(BINDING, endpoint_url)
