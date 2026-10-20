import json
import logging

from app import __version__
from app.domain.errors import UpstreamTimeout


def test_liveness(client, gateway):
    response = client.get("/health")
    assert response.json() == {"status": "ok", "version": __version__}
    assert sum(gateway.calls.values()) == 0  # liveness must not depend on upstream


def test_readiness_ok(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "upstream": "ok"}


def test_readiness_reports_upstream_failure_with_503(client, gateway):
    gateway.error = UpstreamTimeout("slow")
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "upstream": "upstream_timeout"}


def test_liveness_stays_green_when_upstream_is_down(client, gateway):
    gateway.error = UpstreamTimeout("slow")
    assert client.get("/health").status_code == 200


def test_readiness_does_not_use_the_response_cache(client, auth, gateway):
    client.get("/v1/continents", headers=auth)  # continents are now in the response cache
    client.get("/health/ready")
    assert gateway.calls["list_continents"] == 2


def test_readiness_result_is_reused_so_probes_cannot_flood_upstream(client, gateway):
    # The probe is public. HEALTH_CHECK_CACHE_SECONDS (default 5) bounds the upstream calls.
    assert all(client.get("/health/ready").status_code == 200 for _ in range(5))
    assert gateway.calls["list_continents"] == 1


def test_a_request_id_is_generated(client):
    request_id = client.get("/health").headers["x-request-id"]
    assert len(request_id) == 32


def test_a_safe_incoming_request_id_is_reused(client):
    response = client.get("/health", headers={"X-Request-ID": "trace-123.abc_X"})
    assert response.headers["x-request-id"] == "trace-123.abc_X"


def test_an_unsafe_incoming_request_id_is_replaced(client):
    for bad in ["has space", "x" * 200, "semi;colon"]:
        response = client.get("/health", headers={"X-Request-ID": bad})
        assert response.headers["x-request-id"] != bad


def test_a_request_id_with_a_trailing_newline_is_replaced():
    # HTTP clients refuse to send this, so check the helper directly. A `^...$` regex would
    # accept it, because `$` also matches just before a final newline.
    from app.api.middleware import choose_request_id

    assert choose_request_id("abc\n") != "abc\n"
    assert choose_request_id("abc") == "abc"


def test_access_log_has_request_id_key_id_and_cache_status_but_never_the_key(client, auth, caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        client.get("/v1/continents", headers={**auth, "X-Request-ID": "log-test"})
    record = next(r for r in caplog.records if r.name == "app.access")
    assert (record.method, record.path, record.status) == ("GET", "/v1/continents", 200)
    assert record.cache == "MISS"
    assert len(record.key_id) == 12
    assert auth["X-API-Key"] not in json.dumps(record.__dict__, default=str)


def test_security_headers_are_on_every_response(client, auth):
    responses = [
        client.get("/health"),
        client.get("/v1/continents", headers=auth),
        client.get("/v1/continents"),  # 401
        client.get("/v1/countries/ZZ", headers=auth),  # 404
    ]
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"


def test_openapi_documents_error_responses(client):
    spec = client.get("/openapi.json").json()
    responses = spec["paths"]["/v1/countries/{code}"]["get"]["responses"]
    assert {"200", "401", "404", "422", "429", "502", "503", "504"} <= set(responses)


def test_openapi_describes_the_items_of_a_validation_error(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert set(schemas["FieldError"]["properties"]) == {"loc", "msg", "type"}
