"""In-memory stand-ins used instead of the SOAP layer, so tests never touch the network."""

from collections import Counter

from app.domain.errors import DomainError, NotFound
from app.domain.models import Continent, Country, CountrySummary, Currency, Language


def make_country(code: str, name: str, continent: str, currency: str | None, **extra) -> Country:
    return Country(
        code=code,
        name=name,
        capital=extra.pop("capital", f"Capital of {name}"),
        phone_code="1",
        continent_code=continent,
        currency_code=currency,
        flag_url=f"http://example.test/{code}.jpg",
        languages=[Language(code="xx", name="Testish")],
        **extra,
    )


COUNTRIES = [
    make_country("IT", "Italy", "EU", "EUR"),
    make_country("FR", "France", "EU", "EUR"),
    make_country("GB", "United Kingdom", "EU", "GBP"),
    make_country("JP", "Japan", "AS", "JPY"),
    make_country("AQ", "Antarctica", "AN", None),
]


class FakeGateway:
    """Implements the CountryInfoGateway port with canned data.

    `calls` counts how often each method ran, which is how tests prove the cache saved a
    call. Set `error` to make every call raise that domain error.
    """

    def __init__(self, countries: list[Country] | None = None) -> None:
        self.countries = list(COUNTRIES if countries is None else countries)
        self.error: DomainError | None = None
        self.calls: Counter[str] = Counter()

    def _record(self, name: str) -> None:
        self.calls[name] += 1
        if self.error:
            raise self.error

    def get_country(self, code: str) -> Country:
        self._record("get_country")
        for country in self.countries:
            if country.code == code.upper():
                return country
        raise NotFound(f"Country '{code.upper()}' was not found")

    def list_countries(self) -> list[Country]:
        self._record("list_countries")
        return list(self.countries)

    def list_continents(self) -> list[Continent]:
        self._record("list_continents")
        return [Continent(code="EU", name="Europe"), Continent(code="AS", name="Asia")]

    def list_currencies(self) -> list[Currency]:
        self._record("list_currencies")
        return [Currency(code="JPY", name="Yen"), Currency(code="EUR", name="Euro")]

    def countries_using_currency(self, currency_code: str) -> list[CountrySummary]:
        self._record("countries_using_currency")
        return [
            CountrySummary(code=c.code, name=c.name)
            for c in self.countries
            if c.currency_code == currency_code.upper()
        ]


class FakeClock:
    """A clock tests can move by hand: replaces time.monotonic in the cache and limiter."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
