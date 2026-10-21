"""Application settings, loaded from environment variables (and an optional .env file).

Every tunable value lives here, so the rest of the code never reads os.environ directly.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_WSDL_PATH = Path(__file__).parent / "soap" / "wsdl" / "CountryInfoService.wsdl"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "soap-rest-gateway"
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"

    # Upstream SOAP service
    soap_wsdl_path: Path = DEFAULT_WSDL_PATH
    soap_endpoint_url: str = (
        "http://webservices.oorsprong.org/websamples.countryinfo/CountryInfoService.wso"
    )
    soap_timeout_seconds: float = Field(default=5.0, gt=0)
    soap_max_retries: int = Field(default=2, ge=0, le=5)
    soap_retry_backoff_seconds: float = Field(default=0.2, ge=0)
    # Overall budget for one SOAP call including retries: no new attempt starts once it
    # can't fit, and each attempt's timeout is capped to what is left.
    soap_total_timeout_seconds: float = Field(default=15.0, gt=0)
    # The readiness probe must answer quickly, so it uses its own short timeout and no retries.
    health_check_timeout_seconds: float = Field(default=2.0, gt=0)
    # The probe is public: its result is reused this long, so probes can't flood upstream.
    health_check_cache_seconds: float = Field(default=5.0, ge=0)

    # Response cache (in-memory, per process). A TTL of 0 disables caching.
    cache_ttl_seconds: float = Field(default=300.0, ge=0)
    # How long "not found" answers (e.g. an unknown country code) are cached. 0 disables it.
    cache_not_found_ttl_seconds: float = Field(default=60.0, ge=0)
    cache_max_entries: int = Field(default=1024, ge=1)

    # Rate limit per API key: sustained requests per minute, and how many may arrive at once.
    rate_limit_per_minute: int = Field(default=60, ge=1)
    rate_limit_burst: int = Field(default=20, ge=1)

    # Security: comma-separated list in the environment, e.g. API_KEYS=key-one,key-two.
    # NoDecode stops pydantic-settings from expecting JSON, so the validator below can split it.
    # repr=False keeps the keys out of any log line or traceback that prints the settings.
    api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list, repr=False)

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_api_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return [key.strip() for key in value.split(",") if key.strip()]
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    """Build settings once per process; tests can call get_settings.cache_clear()."""
    return Settings()
