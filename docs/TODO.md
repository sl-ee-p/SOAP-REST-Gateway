# TODO

Leftover items from the quality pass. None of them is started in the code; each entry says
what the problem is, where it lives, and the suggested fix.

## 1. Documentation made stale by the last quality-pass commits

- **README, "Caching and rate-limit headers" example** (`README.md`, Examples section). The
  curl output predates the security headers and the move of the rate-limit headers into the
  middleware. Widen the grep to `'^(cache-control|x-content-type|x-cache|x-ratelimit|x-request)'`
  and show the order the middleware now produces: `x-request-id`, `cache-control: no-store`,
  `x-content-type-options: nosniff`, `x-ratelimit-limit`, `x-ratelimit-remaining`, `x-cache`.
  Re-run it against a container if possible (entry 35 says the examples were copied from one).
- **README, rate-limit headers on errors.** Nothing in the README says that 404/422/502
  responses also carry `X-RateLimit-*` (entry 21). Add one sentence under the example or in Errors.
- **README, Errors table** (`README.md`). The 502 row lists only `upstream_fault` /
  `upstream_bad_response`. Since `UpstreamError` maps to 502 (`app/api/errors.py`,
  `STATUS_BY_ERROR`), add "or `upstream_error` for any other upstream failure".
- **DECISIONS entry 17** (`docs/DECISIONS.md`). Mention that the shared `requests` session
  pools 40 connections (`HTTP_POOL_SIZE` in `app/soap/client.py`) to match the 40 worker threads.

## 2. Overall deadline for the retry cycle

- **Problem.** The worst case for a SOAP call is `(retries + 1) × timeout` plus backoff, about
  15 s with the defaults. Up to about 30 s is possible because the timeout applies to connect
  and read separately. There is no total bound (DECISIONS entry 8, "Trade-off").
- **Where.** `SoapCountryInfoGateway._call` in `app/soap/gateway.py`, `build_service` in
  `app/soap/client.py`, `Settings` in `app/config.py`.
- **Suggested fix.**
  - Add `soap_total_timeout_seconds: float = Field(default=15.0, gt=0)`, i.e.
    `SOAP_TOTAL_TIMEOUT_SECONDS`. That uses the `SOAP_*` prefix of the other upstream settings,
    and 15 s is today's documented worst case.
  - Give the gateway `total_timeout_seconds: float | None = None` plus an injectable `clock`
    (default `time.monotonic`) and `sleep` (default `time.sleep`, passed to tenacity's `sleep=`).
  - Compute `deadline = clock() + total` once per call. Replace the stop with a callable that
    stops when `attempt_number > max_retries`, or when
    `deadline - clock() - state.upcoming_sleep < 0.1`. tenacity >= 9 sets `upcoming_sleep`
    before it calls `stop`.
  - Cap each attempt's timeout to the remaining budget. zeep's `Transport.operation_timeout` is
    shared by all threads, so don't mutate it. Use a `ContextVar` cap and a `requests.Session`
    subclass whose `request(self, *args: Any, **kwargs: Any)` lowers `kwargs["timeout"]` to
    `max(min(timeout, cap), 0.1)`. Mount the 40-connection adapter on that session in
    `build_service`.
  - Wire the setting in `build_gateway`, `.env.example`, the README configuration table and
    the Mermaid loop label. Rewrite the entry 8 trade-off: the deadline decides whether another
    attempt starts and caps its timeout. It is not a hard wall-clock limit, because `requests`
    timeouts are per phase and between bytes.
  - Tests:
    - A fake service that advances a `FakeClock` by `min(timeout, cap)` and raises
      `requests.ReadTimeout`. With timeout 4, total 10 and 5 retries, expect caps `[10, 6, 2]`
      and 3 attempts.
    - Backoff 1.0, timeout 4, total 5: one attempt, and the fake sleep is never called.
    - Real zeep with `responses`: `rsps.calls[0].request.req_kwargs["timeout"] == 2` for
      timeout 5 and total 2.
    - A `test_config` case for `soap_total_timeout_seconds=0`.

## 3. Short negative cache for unknown country codes

- **Problem.** `GET /v1/countries/ZZ` calls upstream on every request, because `NotFound` is
  treated like a failure and never cached (`CachedCountryInfoGateway._fetch_once`,
  `app/infra/cached_gateway.py`).
- **Assessment.** This fits cleanly as a separate rule: "not found" is an answer that upstream
  gave deliberately, not a failure. `UpstreamError`s stay uncached.
- **Suggested fix.**
  - Add `cache_not_found_ttl_seconds: float = Field(default=60.0, ge=0)`, i.e.
    `CACHE_NOT_FOUND_TTL_SECONDS`, where 0 disables it.
  - Extend the `Cache` Protocol and `InMemoryTTLCache.set` with an optional
    `ttl_seconds: float | None = None` per-entry override. `CACHE_TTL_SECONDS=0` still disables
    all caching.
  - In the cached gateway, have one `_fetch_and_store` helper, used by both the bypass path
    and the single-flight leader. It catches `NotFound`, stores a frozen `_NotFound(detail)`
    marker with the short TTL, and re-raises. On a hit, a marker raises `NotFound(detail)` and
    counts as a cache `HIT`.
  - Pass the TTL in `create_app` (`app/main.py`).
  - Update `test_errors_are_not_cached` in `tests/unit/test_cached_gateway.py` and
    `tests/api/test_cache.py`. Not-found is cached until the short TTL, then refetched;
    upstream failures are still never cached; bypass still calls upstream.
  - Add a per-entry TTL test in `tests/unit/test_cache.py`.
  - Update DECISIONS entries 22 and 26, add a new entry, and update `.env.example` and the
    README configuration table.

## 4. Error responses documented only as `application/problem+json`

- **Problem.** FastAPI adds the `model` of every extra response under `application/json`, and
  `problem_responses` (`app/api/schemas.py`) adds an empty `application/problem+json` entry
  next to it. So the schema sits under the wrong media type (DECISIONS entry 14, "Known
  limitation").
- **Suggested fix.** In `app/main.py`, subclass `FastAPI` and override `openapi()`. This avoids
  the `# type: ignore[method-assign]` that `app.openapi = ...` needs under mypy. On the first
  build, for every 4xx/5xx response whose content has both media types, merge the
  `application/json` entry into `application/problem+json` and drop `application/json`.
  Readiness's 503 has only `application/json`, which is correct there, and stays untouched.
  - Test: every 4xx/5xx response except `/health/ready`'s has exactly
    `["application/problem+json"]`, with schema `{"$ref": "#/components/schemas/Problem"}`,
    while 200 responses keep `application/json`.
  - Replace the known limitation in entry 14.
