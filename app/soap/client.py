"""Construction of the zeep SOAP client.

Only this module knows how zeep is configured: which WSDL file, which endpoint, which timeout.
"""

from pathlib import Path

import requests
import zeep
from zeep.proxy import ServiceProxy
from zeep.transports import Transport

# Binding name as declared in the WSDL (SOAP 1.1 variant).
BINDING = "{http://www.oorsprong.org/websamples.countryinfo}CountryInfoServiceSoapBinding"


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
    transport = Transport(
        session=session or requests.Session(),
        # Applies to each SOAP call (connect and read separately), not to loading the WSDL.
        operation_timeout=timeout_seconds,
    )
    client = zeep.Client(wsdl=str(wsdl_path), transport=transport)
    return client.create_service(BINDING, endpoint_url)
