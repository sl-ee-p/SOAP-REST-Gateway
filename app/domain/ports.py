"""The interface ("port") the domain needs from the outside world.

The business logic depends on this Protocol, not on the SOAP implementation. That keeps
the domain free of zeep imports and lets tests swap in an in-memory fake.
"""

from typing import Protocol

from app.domain.models import Continent, Country, CountrySummary, Currency


class CountryInfoGateway(Protocol):
    def get_country(self, code: str) -> Country:
        """Return one country. Raises NotFound if the code is unknown."""
        ...

    def list_countries(self) -> list[Country]: ...

    def list_continents(self) -> list[Continent]: ...

    def list_currencies(self) -> list[Currency]: ...

    def countries_using_currency(self, currency_code: str) -> list[CountrySummary]:
        """Return the countries using a currency (empty list if none or unknown)."""
        ...
