import pytest

from tests.conftest import OTHER_API_KEY

PROTECTED = [
    "/v1/countries",
    "/v1/countries/IT",
    "/v1/continents",
    "/v1/currencies",
    "/v1/currencies/EUR/countries",
]


@pytest.mark.parametrize("path", PROTECTED)
def test_every_v1_route_requires_a_key(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "ApiKey"
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "unauthorized"


def test_wrong_key_is_rejected(client):
    assert client.get("/v1/continents", headers={"X-API-Key": "wrong"}).status_code == 401


def test_key_must_match_exactly(client, auth):
    almost = {"X-API-Key": auth["X-API-Key"] + "x"}
    assert client.get("/v1/continents", headers=almost).status_code == 401


def test_empty_key_is_rejected(client):
    assert client.get("/v1/continents", headers={"X-API-Key": ""}).status_code == 401


def test_any_configured_key_is_accepted(client, auth):
    assert client.get("/v1/continents", headers=auth).status_code == 200
    assert client.get("/v1/continents", headers={"X-API-Key": OTHER_API_KEY}).status_code == 200


def test_unauthenticated_requests_never_reach_upstream(client, gateway):
    client.get("/v1/countries")
    assert sum(gateway.calls.values()) == 0


@pytest.mark.parametrize("path", ["/health", "/health/ready", "/openapi.json", "/docs"])
def test_probes_and_docs_are_public(client, path):
    assert client.get(path).status_code == 200


def test_the_api_key_scheme_is_documented_in_openapi(client):
    schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]
    assert any(s["type"] == "apiKey" and s["name"] == "X-API-Key" for s in schemes.values())


def test_app_refuses_to_start_without_api_keys(settings):
    from app.main import create_app

    with pytest.raises(RuntimeError, match="API_KEYS"):
        create_app(settings.model_copy(update={"api_keys": []}))
