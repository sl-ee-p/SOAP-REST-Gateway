def test_first_request_is_a_miss_then_a_hit(client, auth, gateway):
    assert client.get("/v1/continents", headers=auth).headers["x-cache"] == "MISS"
    assert client.get("/v1/continents", headers=auth).headers["x-cache"] == "HIT"
    assert gateway.calls["list_continents"] == 1


def test_no_cache_request_header_bypasses_and_refreshes(client, auth, gateway):
    client.get("/v1/continents", headers=auth)
    bypass = client.get("/v1/continents", headers={**auth, "Cache-Control": "no-cache"})
    assert bypass.headers["x-cache"] == "BYPASS"
    assert gateway.calls["list_continents"] == 2
    assert client.get("/v1/continents", headers=auth).headers["x-cache"] == "HIT"
    assert gateway.calls["list_continents"] == 2


def test_cache_control_is_matched_case_insensitively_among_other_directives(client, auth):
    headers = {**auth, "Cache-Control": "max-age=0, No-Cache"}
    assert client.get("/v1/continents", headers=headers).headers["x-cache"] == "BYPASS"


def test_all_filters_of_the_country_list_share_one_upstream_call(client, auth, gateway):
    client.get("/v1/countries", headers=auth)
    client.get("/v1/countries?continent=EU", headers=auth)
    client.get("/v1/countries?q=ja&limit=1", headers=auth)
    assert gateway.calls["list_countries"] == 1


def test_data_is_fetched_again_after_the_ttl(client, auth, gateway, clock):
    client.get("/v1/continents", headers=auth)
    clock.advance(61)
    response = client.get("/v1/continents", headers=auth)
    assert response.headers["x-cache"] == "MISS"
    assert gateway.calls["list_continents"] == 2


def test_cache_is_shared_between_api_keys(client, auth, gateway):
    from tests.conftest import OTHER_API_KEY

    client.get("/v1/continents", headers=auth)
    client.get("/v1/continents", headers={"X-API-Key": OTHER_API_KEY})
    assert gateway.calls["list_continents"] == 1


def test_not_found_is_cached_until_the_short_ttl(client, auth, gateway, clock):
    first = client.get("/v1/countries/ZZ", headers=auth)
    second = client.get("/v1/countries/ZZ", headers=auth)
    assert (first.status_code, second.status_code) == (404, 404)
    assert (first.headers["x-cache"], second.headers["x-cache"]) == ("MISS", "HIT")
    assert second.json()["detail"] == "Country 'ZZ' was not found"
    assert gateway.calls["get_country"] == 1
    clock.advance(61)
    assert client.get("/v1/countries/ZZ", headers=auth).headers["x-cache"] == "MISS"
    assert gateway.calls["get_country"] == 2


def test_no_cache_header_bypasses_a_cached_not_found(client, auth, gateway):
    client.get("/v1/countries/ZZ", headers=auth)
    bypass = client.get("/v1/countries/ZZ", headers={**auth, "Cache-Control": "no-cache"})
    assert (bypass.status_code, bypass.headers["x-cache"]) == (404, "BYPASS")
    assert gateway.calls["get_country"] == 2


def test_upstream_failures_are_never_cached(client, auth, gateway):
    from app.domain.errors import UpstreamTimeout

    gateway.error = UpstreamTimeout("slow")
    assert client.get("/v1/countries/IT", headers=auth).status_code == 504
    assert client.get("/v1/countries/IT", headers=auth).status_code == 504
    assert gateway.calls["get_country"] == 2


def test_no_cache_header_on_routes_that_do_not_use_the_cache(client):
    assert "x-cache" not in client.get("/health").headers
