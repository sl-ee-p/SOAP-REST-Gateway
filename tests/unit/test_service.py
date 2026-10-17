import pytest

from app.domain.errors import NotFound
from app.domain.service import CountryService, paginate
from tests.fakes import FakeGateway


@pytest.fixture
def service(gateway: FakeGateway) -> CountryService:
    return CountryService(gateway)


def codes(page) -> list[str]:
    return [item.code for item in page.items]


def test_paginate_slices_and_reports_total():
    page = paginate(list(range(10)), limit=3, offset=3)
    assert (page.items, page.total, page.limit, page.offset) == ([3, 4, 5], 10, 3, 3)


def test_paginate_past_the_end_is_empty_not_an_error():
    page = paginate([1, 2], limit=5, offset=10)
    assert page.items == [] and page.total == 2


def test_countries_are_sorted_by_name(service: CountryService):
    page = service.list_countries(limit=50, offset=0)
    assert codes(page) == ["AQ", "FR", "IT", "JP", "GB"]  # Antarctica, France, Italy, Japan, UK


def test_filter_by_continent_ignores_case(service: CountryService):
    assert codes(service.list_countries(limit=50, offset=0, continent="eu")) == ["FR", "IT", "GB"]


def test_filter_by_currency(service: CountryService):
    assert codes(service.list_countries(limit=50, offset=0, currency="eur")) == ["FR", "IT"]


def test_filter_by_name_substring_ignores_case(service: CountryService):
    assert codes(service.list_countries(limit=50, offset=0, name_contains="AN")) == [
        "AQ",
        "FR",
        "JP",
    ]


def test_filters_combine_with_and(service: CountryService):
    page = service.list_countries(limit=50, offset=0, continent="EU", currency="GBP")
    assert codes(page) == ["GB"]


def test_total_counts_all_matches_not_just_the_page(service: CountryService):
    page = service.list_countries(limit=2, offset=0, continent="EU")
    assert len(page.items) == 2 and page.total == 3


def test_unknown_country_propagates_not_found(service: CountryService):
    with pytest.raises(NotFound):
        service.get_country("ZZ")


def test_currency_with_no_countries_is_not_found(service: CountryService):
    with pytest.raises(NotFound, match="XXX"):
        service.countries_using_currency("xxx", limit=10, offset=0)


def test_countries_using_currency_are_sorted_by_name(service: CountryService):
    page = service.countries_using_currency("EUR", limit=10, offset=0)
    assert [c.name for c in page.items] == ["France", "Italy"]
