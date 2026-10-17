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


def test_readiness_is_not_served_from_the_cache(client, gateway):
    client.get("/health/ready")
    client.get("/health/ready")
    assert gateway.calls["list_continents"] == 2


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


def test_access_log_has_request_id_key_id_and_cache_status_but_never_the_key(client, auth, caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        client.get("/v1/continents", headers={**auth, "X-Request-ID": "log-test"})
    record = next(r for r in caplog.records if r.name == "app.access")
    assert (record.method, record.path, record.status) == ("GET", "/v1/continents", 200)
    assert record.cache == "MISS"
    assert len(record.key_id) == 12
    assert auth["X-API-Key"] not in json.dumps(record.__dict__, default=str)


def test_openapi_documents_error_responses(client):
    spec = client.get("/openapi.json").json()
    responses = spec["paths"]["/v1/countries/{code}"]["get"]["responses"]
    assert {"200", "401", "404", "422", "429", "502", "503", "504"} <= set(responses)
