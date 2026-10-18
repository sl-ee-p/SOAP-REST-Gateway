import pytest
from pydantic import ValidationError

from app.config import Settings


def make(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_api_keys_are_split_from_a_comma_separated_string(monkeypatch):
    monkeypatch.setenv("API_KEYS", " one , two,,three ")
    assert make().api_keys == ["one", "two", "three"]


def test_api_keys_default_to_empty(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    assert make().api_keys == []


def test_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("CACHE_TTL_SECONDS", "12.5")
    monkeypatch.setenv("SOAP_MAX_RETRIES", "4")
    settings = make()
    assert (settings.cache_ttl_seconds, settings.soap_max_retries) == (12.5, 4)


def test_log_level_is_uppercased():
    assert make(log_level="debug").log_level == "DEBUG"


def test_unknown_log_level_fails_at_startup():
    # Without validation this only failed later, inside logging.setLevel().
    with pytest.raises(ValidationError, match="log_level"):
        make(log_level="verbose")


def test_api_keys_are_not_in_the_settings_repr():
    settings = make(api_keys=["super-secret-key-0123456789"])
    assert "super-secret" not in repr(settings)
    assert "super-secret" not in str(settings)


@pytest.mark.parametrize(
    "field, value",
    [("soap_timeout_seconds", 0), ("soap_max_retries", 99), ("rate_limit_burst", 0)],
)
def test_invalid_values_fail_at_startup(field, value):
    with pytest.raises(ValidationError):
        make(**{field: value})


def test_get_settings_is_cached_per_process(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("API_KEYS", "abc")
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
