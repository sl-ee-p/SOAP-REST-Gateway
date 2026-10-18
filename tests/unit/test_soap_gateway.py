"""SOAP layer tests: real zeep and real WSDL, with the HTTP transport faked by `responses`.

Nothing here touches the network: `responses` refuses any request that has no registered reply.
"""

from pathlib import Path

import pytest
import requests
import responses

from app.config import DEFAULT_WSDL_PATH, Settings
from app.domain.errors import (
    NotFound,
    UpstreamBadResponse,
    UpstreamFault,
    UpstreamTimeout,
    UpstreamUnavailable,
)
from app.soap.client import build_service
from app.soap.gateway import SoapCountryInfoGateway, build_gateway

ENDPOINT = "http://soap.test/CountryInfoService.wso"
FIXTURES = Path(__file__).parent.parent / "fixtures" / "soap"


def xml(name: str) -> str:
    return (FIXTURES / name).read_text()


def reply(rsps: responses.RequestsMock, name: str, status: int = 200) -> None:
    rsps.post(ENDPOINT, body=xml(name), status=status, content_type="text/xml")


@pytest.fixture
def gateway() -> SoapCountryInfoGateway:
    service = build_service(DEFAULT_WSDL_PATH, ENDPOINT, timeout_seconds=1)
    # No waiting between retries: the tests check *whether* it retries, not the delay.
    return SoapCountryInfoGateway(service, max_retries=2, backoff_seconds=0)


@pytest.fixture
def rsps():
    """Fake HTTP layer. A request without a registered reply raises, so nothing leaks out."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


# --- mapping -------------------------------------------------------------------------------


def test_get_country_maps_soap_fields_to_the_domain_model(gateway, rsps):
    reply(rsps, "country_it.xml")
    country = gateway.get_country("IT")
    assert country.code == "IT"
    assert country.name == "Italy"
    assert country.capital == "Rome"
    assert country.phone_code == "39"
    assert country.continent_code == "EU"
    assert country.currency_code == "EUR"
    assert country.flag_url.endswith("Italy.jpg")
    assert [(lang.code, lang.name) for lang in country.languages] == [("ita", "Italian")]


def test_country_code_is_uppercased_and_trimmed_before_the_call(gateway, rsps):
    reply(rsps, "country_it.xml")
    gateway.get_country(" it ")
    assert "<ns0:sCountryISOCode>IT</ns0:sCountryISOCode>" in rsps.calls[0].request.body.decode()


def test_unknown_country_is_not_found_even_though_upstream_answers_200(gateway, rsps):
    reply(rsps, "country_not_found.xml")
    with pytest.raises(NotFound, match="'ZZ'"):
        gateway.get_country("ZZ")


def test_empty_upstream_strings_become_null_and_missing_languages_an_empty_list(gateway, rsps):
    reply(rsps, "countries_all.xml")
    france, antarctica = gateway.list_countries()
    assert france.name == "France" and len(france.languages) == 1
    assert antarctica.code == "AQ"
    assert antarctica.capital is None
    assert antarctica.currency_code is None
    assert antarctica.flag_url is None
    assert antarctica.languages == []


def test_list_continents(gateway, rsps):
    reply(rsps, "continents.xml")
    assert [(c.code, c.name) for c in gateway.list_continents()] == [
        ("AF", "Africa"),
        ("EU", "Europe"),
    ]


def test_currency_placeholder_without_a_code_is_dropped(gateway, rsps):
    reply(rsps, "currencies.xml")
    assert [c.code for c in gateway.list_currencies()] == ["EUR"]


def test_countries_using_currency(gateway, rsps):
    reply(rsps, "currency_countries.xml")
    assert [c.code for c in gateway.countries_using_currency("eur")] == ["FR", "IT"]


def test_unknown_currency_returns_an_empty_list(gateway, rsps):
    reply(rsps, "currency_countries_empty.xml")
    assert gateway.countries_using_currency("XXX") == []


# --- failure handling ----------------------------------------------------------------------


def test_soap_fault_becomes_upstream_fault_and_is_not_retried(gateway, rsps):
    reply(rsps, "fault.xml", status=500)
    with pytest.raises(UpstreamFault, match="Something broke upstream"):
        gateway.list_continents()
    assert len(rsps.calls) == 1


def test_transient_http_error_is_retried_until_it_succeeds(gateway, rsps):
    rsps.post(ENDPOINT, status=503)
    reply(rsps, "continents.xml")
    assert len(gateway.list_continents()) == 2
    assert len(rsps.calls) == 2


def test_persistent_503_gives_up_after_all_attempts(gateway, rsps):
    rsps.post(ENDPOINT, status=503)
    with pytest.raises(UpstreamBadResponse, match="HTTP 503"):
        gateway.list_continents()
    assert len(rsps.calls) == 3  # 1 try + 2 retries


def test_client_error_status_is_not_retried(gateway, rsps):
    rsps.post(ENDPOINT, status=400)
    with pytest.raises(UpstreamBadResponse):
        gateway.list_continents()
    assert len(rsps.calls) == 1


def test_timeout_is_retried_then_reported_as_upstream_timeout(gateway, rsps):
    rsps.post(ENDPOINT, body=requests.ReadTimeout())
    with pytest.raises(UpstreamTimeout):
        gateway.list_continents()
    assert len(rsps.calls) == 3


def test_connect_timeout_counts_as_a_timeout_not_as_unreachable(gateway, rsps):
    rsps.post(ENDPOINT, body=requests.ConnectTimeout())
    with pytest.raises(UpstreamTimeout):
        gateway.list_continents()


def test_connection_failure_is_retried_then_reported_as_unavailable(gateway, rsps):
    rsps.post(ENDPOINT, body=requests.ConnectionError("refused"))
    with pytest.raises(UpstreamUnavailable):
        gateway.list_continents()
    assert len(rsps.calls) == 3


def test_retries_can_be_disabled(rsps):
    service = build_service(DEFAULT_WSDL_PATH, ENDPOINT, timeout_seconds=1)
    no_retry = SoapCountryInfoGateway(service, max_retries=0, backoff_seconds=0)
    rsps.post(ENDPOINT, body=requests.ConnectionError())
    with pytest.raises(UpstreamUnavailable):
        no_retry.list_continents()
    assert len(rsps.calls) == 1


def test_unparseable_response_becomes_bad_response(gateway, rsps):
    rsps.post(ENDPOINT, body="this is not xml", content_type="text/xml")
    with pytest.raises(UpstreamBadResponse):
        gateway.list_continents()


def test_unexpected_exception_from_the_client_becomes_bad_response():
    class BrokenService:
        def ListOfContinentsByCode(self):  # noqa: N802 - mirrors the SOAP operation name
            raise ValueError("zeep exploded")

    gateway = SoapCountryInfoGateway(BrokenService(), max_retries=2, backoff_seconds=0)
    with pytest.raises(UpstreamBadResponse):
        gateway.list_continents()


# --- build_gateway (the factory used by the app and the readiness probe) --------------------


def test_build_gateway_uses_settings_for_retries(rsps):
    settings = Settings(_env_file=None, api_keys=["k"], soap_endpoint_url=ENDPOINT)
    settings = settings.model_copy(update={"soap_max_retries": 2, "soap_retry_backoff_seconds": 0})
    rsps.post(ENDPOINT, status=503)
    with pytest.raises(UpstreamBadResponse):
        build_gateway(settings).list_continents()
    assert len(rsps.calls) == 3


def test_build_gateway_overrides_win_over_settings(rsps):
    settings = Settings(_env_file=None, api_keys=["k"], soap_endpoint_url=ENDPOINT)
    rsps.post(ENDPOINT, status=503)
    with pytest.raises(UpstreamBadResponse):
        build_gateway(settings, timeout_seconds=0.5, max_retries=0).list_continents()
    assert len(rsps.calls) == 1  # readiness probe: no retries


# --- malformed upstream data ---------------------------------------------------------------


def envelope(operation: str, result: str, prolog: str = "") -> str:
    """A minimal SOAP response for `operation` whose Result element holds `result`."""
    return (
        f'<?xml version="1.0" encoding="utf-8"?>{prolog}'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><soap:Body>'
        f'<m:{operation}Response xmlns:m="http://www.oorsprong.org/websamples.countryinfo">'
        f"<m:{operation}Result>{result}</m:{operation}Result>"
        f"</m:{operation}Response></soap:Body></soap:Envelope>"
    )


def test_record_with_a_missing_required_field_is_a_bad_response_not_a_crash(gateway, rsps):
    body = envelope("ListOfContinentsByCode", "<m:tContinent><m:sCode>AF</m:sCode></m:tContinent>")
    rsps.post(ENDPOINT, body=body, content_type="text/xml")
    with pytest.raises(UpstreamBadResponse, match="incomplete record"):
        gateway.list_continents()


def test_country_with_a_code_but_no_name_is_a_bad_response(gateway, rsps):
    body = envelope("FullCountryInfo", "<m:sISOCode>IT</m:sISOCode>")
    rsps.post(ENDPOINT, body=body, content_type="text/xml")
    with pytest.raises(UpstreamBadResponse):
        gateway.get_country("IT")


def test_malformed_record_is_a_502_through_the_api(rsps):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import API_KEY

    settings = Settings(
        _env_file=None,
        api_keys=[API_KEY],
        soap_endpoint_url=ENDPOINT,
        soap_max_retries=0,
        log_level="CRITICAL",
    )
    body = envelope("ListOfContinentsByCode", "<m:tContinent><m:sCode>AF</m:sCode></m:tContinent>")
    rsps.post(ENDPOINT, body=body, content_type="text/xml")
    response = TestClient(create_app(settings)).get(
        "/v1/continents", headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 502
    assert response.json()["code"] == "upstream_bad_response"
