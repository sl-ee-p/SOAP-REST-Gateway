"""Readiness check: is the upstream SOAP service answering?

The probe is public (orchestrators call it without a key), so it must not let anyone turn
our traffic into load on upstream. The result is reused for a few seconds, and probes that
arrive while a check is running wait for that check instead of starting their own.
"""

import threading
import time
from collections.abc import Callable

from app.domain.errors import UpstreamError
from app.domain.ports import CountryInfoGateway


class ReadinessCheck:
    def __init__(
        self,
        gateway: CountryInfoGateway,
        reuse_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gateway = gateway
        self._reuse_seconds = reuse_seconds
        self._clock = clock  # injectable so tests don't need to sleep
        self._lock = threading.Lock()
        self._checked_at: float | None = None
        self._error_code: str | None = None

    def check(self) -> str | None:
        """Return None if upstream answers, otherwise the error code of the failed check."""
        # One check at a time: concurrent probes wait here, then reuse its fresh result.
        with self._lock:
            checked_at = self._checked_at
            if checked_at is not None and self._clock() - checked_at < self._reuse_seconds:
                return self._error_code
            try:
                # One cheap call. It uses its own gateway, never the response cache, which
                # could hide an outage for a whole TTL.
                self._gateway.list_continents()
                self._error_code = None
            except UpstreamError as exc:
                self._error_code = exc.code
            self._checked_at = self._clock()
            return self._error_code
