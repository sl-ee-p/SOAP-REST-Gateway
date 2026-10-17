"""SOAP implementation of the CountryInfoGateway port.

Responsibilities, and nothing else:
1. call SOAP operations, with retries for transient network failures;
2. translate technical failures into domain errors;
3. map SOAP objects (`sISOCode`, `tLanguage`, ...) to domain models, smoothing over the
   quirks of this particular service.
"""

import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

import requests
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from zeep import exceptions as zeep_exc
from zeep.proxy import ServiceProxy

from app.config import Settings
from app.domain.errors import (
    NotFound,
    UpstreamBadResponse,
    UpstreamError,
    UpstreamFault,
    UpstreamTimeout,
    UpstreamUnavailable,
)
from app.domain.models import Continent, Country, CountrySummary, Currency, Language
from app.soap.client import build_service

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP statuses that usually mean "try again shortly" (overloaded or restarting server).
_RETRYABLE_HTTP_STATUSES = {502, 503, 504}


def _is_transient(exc: BaseException) -> bool:
    """Retry only failures that may succeed on a second try.

    A SOAP Fault is the server deliberately saying "no", so retrying it is pointless.
    """
    if isinstance(exc, requests.Timeout | requests.ConnectionError):
        return True
    return isinstance(exc, zeep_exc.TransportError) and exc.status_code in _RETRYABLE_HTTP_STATUSES


def _translate(exc: Exception, operation: str) -> UpstreamError:
    # Timeout first: requests.ConnectTimeout is both a Timeout and a ConnectionError.
    if isinstance(exc, requests.Timeout):
        return UpstreamTimeout(f"{operation}: upstream service timed out")
    if isinstance(exc, requests.ConnectionError):
        return UpstreamUnavailable(f"{operation}: upstream service is unreachable")
    if isinstance(exc, zeep_exc.Fault):
        return UpstreamFault(f"{operation}: upstream returned a SOAP fault: {exc.message}")
    if isinstance(exc, zeep_exc.TransportError):
        return UpstreamBadResponse(f"{operation}: upstream returned HTTP {exc.status_code}")
    return UpstreamBadResponse(f"{operation}: upstream returned an unexpected response")


class SoapCountryInfoGateway:
    def __init__(
        self,
        service: ServiceProxy,
        max_retries: int = 2,
        backoff_seconds: float = 0.2,
    ) -> None:
        self._service = service
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    # --- public API (the CountryInfoGateway port) ---------------------------------------

    def get_country(self, code: str) -> Country:
        # Quirk: the service only matches upper-case codes ("it" is "not found").
        code = code.strip().upper()
        result = self._call("FullCountryInfo", sCountryISOCode=code)
        # Quirk: an unknown code is not a SOAP Fault. The service returns a normal response
        # whose name is "Country not found in the database" and whose code is empty.
        if result is None or not result.sISOCode:
            raise NotFound(f"Country '{code}' was not found")
        return _to_country(result)

    def list_countries(self) -> list[Country]:
        return [_to_country(item) for item in self._call("FullCountryInfoAllCountries") or []]

    def list_continents(self) -> list[Continent]:
        return [
            Continent(code=item.sCode, name=item.sName)
            for item in self._call("ListOfContinentsByCode") or []
        ]

    def list_currencies(self) -> list[Currency]:
        return [
            Currency(code=item.sISOCode, name=item.sName)
            for item in self._call("ListOfCurrenciesByCode") or []
            # Quirk: the list contains a placeholder "No Universal Currency" with no code.
            if item.sISOCode
        ]

    def countries_using_currency(self, currency_code: str) -> list[CountrySummary]:
        code = currency_code.strip().upper()
        # Quirk: an unknown currency returns nothing at all (None), not an empty list.
        result = self._call("CountriesUsingCurrency", sISOCurrencyCode=code) or []
        return [CountrySummary(code=item.sISOCode, name=item.sName) for item in result]

    # --- internals ----------------------------------------------------------------------

    def _call(self, operation: str, **params: Any) -> Any:
        """Invoke a SOAP operation with retries, logging and error translation."""
        method: Callable[..., Any] = getattr(self._service, operation)
        retrying = Retrying(
            stop=stop_after_attempt(self._max_retries + 1),
            # 0.2s, 0.4s, 0.8s ... plus random jitter so many clients don't retry in lockstep.
            wait=wait_exponential_jitter(
                multiplier=self._backoff_seconds, jitter=self._backoff_seconds, max=2.0
            ),
            retry=retry_if_exception(_is_transient),
            before_sleep=lambda state: _log_retry(operation, state),
            reraise=True,
        )
        started = time.perf_counter()
        try:
            result = retrying(method, **params)
        except Exception as exc:
            error = _translate(exc, operation)
            logger.warning(
                "soap_call_failed",
                extra={
                    "operation": operation,
                    "error_code": error.code,
                    "detail": error.detail,
                    "attempts": retrying.statistics.get("attempt_number", 1),
                    "duration_ms": _elapsed_ms(started),
                },
            )
            raise error from exc
        logger.info(
            "soap_call",
            extra={
                "operation": operation,
                "attempts": retrying.statistics.get("attempt_number", 1),
                "duration_ms": _elapsed_ms(started),
            },
        )
        return result


def _to_country(item: Any) -> Country:
    languages = item.Languages.tLanguage if item.Languages is not None else []
    return Country(
        code=item.sISOCode,
        name=item.sName,
        capital=item.sCapitalCity or None,
        phone_code=item.sPhoneCode or None,
        continent_code=item.sContinentCode or None,
        currency_code=item.sCurrencyISOCode or None,
        flag_url=item.sCountryFlag or None,
        languages=[Language(code=lang.sISOCode, name=lang.sName) for lang in languages or []],
    )


def _log_retry(operation: str, state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    logger.warning(
        "soap_call_retry",
        extra={
            "operation": operation,
            "attempt": state.attempt_number,
            "error": type(exc).__name__ if exc else None,
        },
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def build_gateway(
    settings: Settings,
    *,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> SoapCountryInfoGateway:
    """Wire a gateway from configuration (used by the app and the smoke script).

    The keyword overrides let the readiness probe use a short timeout and no retries.
    """
    service = build_service(
        wsdl_path=settings.soap_wsdl_path,
        endpoint_url=settings.soap_endpoint_url,
        timeout_seconds=timeout_seconds or settings.soap_timeout_seconds,
    )
    return SoapCountryInfoGateway(
        service,
        max_retries=settings.soap_max_retries if max_retries is None else max_retries,
        backoff_seconds=settings.soap_retry_backoff_seconds,
    )
