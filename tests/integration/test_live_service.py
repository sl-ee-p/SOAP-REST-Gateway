"""Tests against the real CountryInfoService. Deselected by default (see pyproject.toml).

Run them with:  pytest -m integration
They need internet access and depend on a third-party sample service, so a failure here can
mean "the upstream is down" rather than "our code is broken".
"""

import pytest
import requests

from app.config import DEFAULT_WSDL_PATH, Settings
from app.domain.errors import NotFound
from app.soap.gateway import build_gateway

pytestmark = pytest.mark.integration

SETTINGS = Settings(_env_file=None, api_keys=["unused"], soap_timeout_seconds=15)


@pytest.fixture(scope="module")
def gateway():
    return build_gateway(SETTINGS)


def test_get_country(gateway):
    italy = gateway.get_country("it")
    assert (italy.code, italy.name, italy.capital) == ("IT", "Italy", "Rome")
    assert italy.currency_code == "EUR"
    assert any(lang.name == "Italian" for lang in italy.languages)


def test_unknown_country_is_not_found(gateway):
    with pytest.raises(NotFound):
        gateway.get_country("ZZ")


def test_list_countries(gateway):
    countries = gateway.list_countries()
    assert len(countries) > 200
    assert all(c.code and c.name for c in countries)


def test_list_continents(gateway):
    assert {"EU", "AS", "AF"} <= {c.code for c in gateway.list_continents()}


def test_currency_list_has_no_codeless_placeholder(gateway):
    currencies = gateway.list_currencies()
    assert "EUR" in {c.code for c in currencies}
    assert all(c.code for c in currencies)


def test_countries_using_euro_include_italy(gateway):
    assert "IT" in {c.code for c in gateway.countries_using_currency("EUR")}


def test_unknown_currency_has_no_countries(gateway):
    assert gateway.countries_using_currency("XXX") == []


def test_full_stack_through_the_rest_api():
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(SETTINGS.model_copy(update={"api_keys": ["k" * 20]})))
    response = client.get("/v1/countries/IT", headers={"X-API-Key": "k" * 20})
    assert response.status_code == 200
    assert response.json()["name"] == "Italy"


def test_committed_wsdl_matches_the_live_contract():
    """Detects upstream contract changes: if this fails, review the diff and refresh the WSDL."""
    live = requests.get(f"{SETTINGS.soap_endpoint_url}?WSDL", timeout=15).text
    committed = DEFAULT_WSDL_PATH.read_text()

    def normalise(text: str) -> list[str]:
        return [line.strip() for line in text.replace("\r", "").splitlines() if line.strip()]

    assert normalise(live) == normalise(committed)
