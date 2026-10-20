import pytest

from app.domain.errors import (
    UpstreamBadResponse,
    UpstreamError,
    UpstreamFault,
    UpstreamTimeout,
    UpstreamUnavailable,
)


def assert_problem(response, status: int, code: str) -> dict:
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["instance"]
    return body


def test_unknown_country_is_404(client, auth):
    body = assert_problem(client.get("/v1/countries/ZZ", headers=auth), 404, "not_found")
    assert body["detail"] == "Country 'ZZ' was not found"


def test_unknown_currency_is_404(client, auth):
    assert_problem(client.get("/v1/currencies/XXX/countries", headers=auth), 404, "not_found")


def test_unknown_route_is_a_problem_response_too(client, auth):
    assert_problem(client.get("/v1/nothing", headers=auth), 404, "not_found")


def test_wrong_method_is_a_405_problem_with_allow(client, auth):
    response = client.post("/v1/continents", headers=auth)
    assert_problem(response, 405, "method_not_allowed")
    assert "GET" in response.headers["allow"]


@pytest.mark.parametrize(
    "path",
    [
        "/v1/countries/ITA",  # too long
        "/v1/countries/I1",  # not letters
        "/v1/currencies/EU/countries",  # currency codes have 3 letters
        "/v1/countries?limit=0",
        "/v1/countries?limit=251",
        "/v1/countries?offset=-1",
        "/v1/countries?limit=abc",
        "/v1/countries?continent=EUR",
        "/v1/countries?q=",
    ],
)
def test_invalid_input_is_422_with_field_errors(client, auth, path):
    body = assert_problem(client.get(path, headers=auth), 422, "validation_error")
    assert body["errors"] and {"loc", "msg", "type"} <= set(body["errors"][0])


def test_invalid_input_never_reaches_upstream(client, auth, gateway):
    client.get("/v1/countries/ITA", headers=auth)
    assert sum(gateway.calls.values()) == 0


@pytest.mark.parametrize(
    "error, status, code",
    [
        (UpstreamFault("x"), 502, "upstream_fault"),
        (UpstreamBadResponse("x"), 502, "upstream_bad_response"),
        (UpstreamUnavailable("x"), 503, "upstream_unavailable"),
        (UpstreamTimeout("x"), 504, "upstream_timeout"),
        # The base class: a future upstream error without its own entry is a 502 too.
        (UpstreamError("x"), 502, "upstream_error"),
    ],
)
def test_upstream_failures_map_to_gateway_statuses(client, auth, gateway, error, status, code):
    gateway.error = error
    assert_problem(client.get("/v1/continents", headers=auth), status, code)


def test_upstream_internals_are_not_leaked_to_clients(client, auth, gateway):
    gateway.error = UpstreamFault("ListOfContinentsByCode: SQL error at db-7.internal")
    body = client.get("/v1/continents", headers=auth).json()
    assert "db-7" not in str(body) and "ListOfContinents" not in str(body)


def test_unexpected_exception_is_a_generic_500(client, auth, gateway):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    gateway.list_continents = boom
    response = client.get("/v1/continents", headers=auth)
    body = assert_problem(response, 500, "internal_error")
    assert "secret" not in response.text
    assert body["detail"] == "An unexpected error occurred."


def test_domain_error_without_a_mapping_is_a_500(client, auth, gateway):
    from app.domain.errors import DomainError

    gateway.error = DomainError("unmapped")
    assert_problem(client.get("/v1/continents", headers=auth), 500, "internal_error")
