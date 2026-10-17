from tests.conftest import OTHER_API_KEY


def test_headers_report_the_remaining_budget(client, auth):
    first = client.get("/v1/continents", headers=auth)
    second = client.get("/v1/continents", headers=auth)
    assert first.headers["x-ratelimit-limit"] == "3"
    assert first.headers["x-ratelimit-remaining"] == "2"
    assert second.headers["x-ratelimit-remaining"] == "1"


def test_request_over_the_limit_gets_429_with_retry_after(client, auth):
    for _ in range(3):
        assert client.get("/v1/continents", headers=auth).status_code == 200
    response = client.get("/v1/continents", headers=auth)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1
    assert response.headers["x-ratelimit-remaining"] == "0"
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "too_many_requests"


def test_limit_applies_per_key(client, auth):
    for _ in range(4):
        client.get("/v1/continents", headers=auth)
    other = client.get("/v1/continents", headers={"X-API-Key": OTHER_API_KEY})
    assert other.status_code == 200


def test_access_resumes_after_the_bucket_refills(client, auth, clock):
    for _ in range(4):
        client.get("/v1/continents", headers=auth)
    assert client.get("/v1/continents", headers=auth).status_code == 429
    clock.advance(2)  # 60/min = 1 token per second
    assert client.get("/v1/continents", headers=auth).status_code == 200


def test_rejected_requests_do_not_reach_upstream(client, auth, gateway):
    for _ in range(3):
        client.get("/v1/continents", headers=auth)
    gateway.calls.clear()
    client.get("/v1/continents", headers=auth)
    assert sum(gateway.calls.values()) == 0


def test_health_probes_are_not_rate_limited(client):
    assert all(client.get("/health").status_code == 200 for _ in range(10))
