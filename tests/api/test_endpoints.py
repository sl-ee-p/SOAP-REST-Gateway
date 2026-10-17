def get(client, auth, path, **params):
    return client.get(path, headers=auth, params=params)


def test_get_country_returns_clean_json(client, auth):
    response = get(client, auth, "/v1/countries/it")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "IT"
    assert set(body) == {
        "code", "name", "capital", "phone_code", "continent_code",
        "currency_code", "flag_url", "languages",
    }  # fmt: skip
    assert body["languages"] == [{"code": "xx", "name": "Testish"}]


def test_list_countries_uses_the_pagination_envelope(client, auth):
    body = get(client, auth, "/v1/countries", limit=2, offset=1).json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert (body["total"], body["limit"], body["offset"]) == (5, 2, 1)
    assert [c["code"] for c in body["items"]] == ["FR", "IT"]


def test_list_countries_filters(client, auth):
    body = get(client, auth, "/v1/countries", continent="eu", currency="eur", q="ITA").json()
    assert [c["code"] for c in body["items"]] == ["IT"]
    assert body["total"] == 1


def test_offset_past_the_end_returns_an_empty_page(client, auth):
    response = get(client, auth, "/v1/countries", offset=100)
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_continents(client, auth):
    body = get(client, auth, "/v1/continents").json()
    assert [c["code"] for c in body["items"]] == ["AS", "EU"]


def test_currencies_are_sorted_by_code(client, auth):
    body = get(client, auth, "/v1/currencies").json()
    assert [c["code"] for c in body["items"]] == ["EUR", "JPY"]


def test_countries_using_a_currency(client, auth):
    body = get(client, auth, "/v1/currencies/eur/countries").json()
    assert body["items"] == [{"code": "FR", "name": "France"}, {"code": "IT", "name": "Italy"}]
    assert body["total"] == 2


def test_root_redirects_to_the_docs(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"
