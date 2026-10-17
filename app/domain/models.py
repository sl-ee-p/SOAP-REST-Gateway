"""Domain models: the clean shapes the rest of the app works with.

They are deliberately independent of SOAP naming (`sISOCode`, `tLanguage`, ...).
Models are frozen (immutable) so cached instances can be shared safely between requests.
"""

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class Language(DomainModel):
    code: str = Field(description="ISO 639 language code", examples=["ita"])
    name: str = Field(examples=["Italian"])


class Continent(DomainModel):
    code: str = Field(description="Two-letter continent code", examples=["EU"])
    name: str = Field(examples=["Europe"])


class Currency(DomainModel):
    code: str = Field(description="ISO 4217 currency code", examples=["EUR"])
    name: str = Field(examples=["Euro"])


class CountrySummary(DomainModel):
    code: str = Field(description="ISO 3166-1 alpha-2 country code", examples=["IT"])
    name: str = Field(examples=["Italy"])


class Country(CountrySummary):
    capital: str | None = Field(default=None, examples=["Rome"])
    phone_code: str | None = Field(default=None, examples=["39"])
    continent_code: str | None = Field(default=None, examples=["EU"])
    currency_code: str | None = Field(default=None, examples=["EUR"])
    flag_url: str | None = Field(
        default=None,
        examples=["http://www.oorsprong.org/WebSamples.CountryInfo/Flags/Italy.jpg"],
    )
    languages: list[Language] = Field(default_factory=list)
