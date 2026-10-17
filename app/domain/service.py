"""Business logic: what the REST API offers on top of the raw upstream operations.

The service only talks to the CountryInfoGateway port, so it can be tested with a fake.
Filtering, sorting and pagination happen here: the upstream has no such features, so we
fetch the full (small) lists once and slice them in memory.
"""

from collections.abc import Sequence
from typing import TypeVar

from app.domain.errors import NotFound
from app.domain.models import Continent, Country, CountrySummary, Currency, Page
from app.domain.ports import CountryInfoGateway

T = TypeVar("T")


def paginate(items: Sequence[T], limit: int, offset: int) -> Page[T]:
    return Page[T](
        items=list(items[offset : offset + limit]), total=len(items), limit=limit, offset=offset
    )


class CountryService:
    def __init__(self, gateway: CountryInfoGateway) -> None:
        self._gateway = gateway

    def get_country(self, code: str) -> Country:
        return self._gateway.get_country(code)

    def list_countries(
        self,
        *,
        limit: int,
        offset: int,
        continent: str | None = None,
        currency: str | None = None,
        name_contains: str | None = None,
    ) -> Page[Country]:
        countries = self._gateway.list_countries()
        if continent:
            countries = [c for c in countries if c.continent_code == continent.upper()]
        if currency:
            countries = [c for c in countries if c.currency_code == currency.upper()]
        if name_contains:
            needle = name_contains.casefold()
            countries = [c for c in countries if needle in c.name.casefold()]
        countries = sorted(countries, key=lambda c: c.name.casefold())
        return paginate(countries, limit, offset)

    def list_continents(self, *, limit: int, offset: int) -> Page[Continent]:
        continents = sorted(self._gateway.list_continents(), key=lambda c: c.code)
        return paginate(continents, limit, offset)

    def list_currencies(self, *, limit: int, offset: int) -> Page[Currency]:
        currencies = sorted(self._gateway.list_currencies(), key=lambda c: c.code)
        return paginate(currencies, limit, offset)

    def countries_using_currency(
        self, currency_code: str, *, limit: int, offset: int
    ) -> Page[CountrySummary]:
        countries = self._gateway.countries_using_currency(currency_code)
        # Business rule: a currency used by no country is treated as unknown (404), so
        # clients can tell "typo in the code" apart from a real, valid answer.
        if not countries:
            raise NotFound(f"Currency '{currency_code.upper()}' was not found")
        return paginate(sorted(countries, key=lambda c: c.name.casefold()), limit, offset)
