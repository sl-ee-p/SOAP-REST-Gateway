# Design decisions

Each entry explains a choice in plain language: the context, what was decided, which
alternatives were considered, and what it costs. Entries are added phase by phase.

---

## 1. Upstream: CountryInfoService

**Context.** The project needs a free, public, stable SOAP service with useful data and no sign-up.

**Decision.** Use the CountryInfoService sample by oorsprong.org. It has 21 read-only operations
(countries, continents, currencies, languages). I checked that it was reachable before choosing it.

**Alternatives.** Calculator-style demo services (too trivial to show real mapping work), and
government or EU SOAP APIs (they need registration, or their schemas change).

**Trade-off.** It is a sample service with no SLA, so the gateway must assume it can be slow or
down. That is a good reason for the timeouts, retries and caching that come later.

## 2. The upstream only works over plain HTTP

**Context.** The WSDL and endpoint answer on `http://`. `https://` does not respond.

**Decision.** Call it over HTTP and keep the URL configurable (`SOAP_ENDPOINT_URL`), so switching
to HTTPS is a config change, not a code change.

**Trade-off.** The traffic between the gateway and upstream is unencrypted. That is acceptable
here because the data is public, read-only and no credentials are sent upstream. With a real
partner API this would be a blocker.

## 3. Python stack: FastAPI + zeep + Pydantic

**Decision.**
- **FastAPI** generates OpenAPI docs from type hints, validates input with Pydantic, and has a
  simple dependency-injection system that is used later for auth, rate limiting and caching.
- **zeep** is the standard, maintained SOAP client for Python. It reads the WSDL and exposes each
  operation as a Python method, so there is no hand-built XML.
- **Pydantic** models define the clean JSON shapes and validate data at the boundaries.

**Alternatives.** Flask (no built-in validation or OpenAPI), and hand-written XML with `requests`
(fragile and verbose; this is the kind of work zeep exists to remove).

## 4. Layered architecture with a "port"

**Context.** Requirement: keep SOAP code, business logic and API routes in separate modules.

**Decision.** There are four layers. Each one only talks to the one below it:

| Layer | Folder | Knows about |
|---|---|---|
| HTTP | `app/api/` | FastAPI, status codes, headers |
| Business logic | `app/domain/` | domain models and errors only |
| Integration | `app/soap/` | zeep, SOAP field names, upstream quirks |
| Config / logging | `app/config.py`, `app/logging_config.py` | environment |

The domain defines the interface it needs, `CountryInfoGateway` in `app/domain/ports.py`
(a `typing.Protocol`, similar to a PHP interface). The SOAP code implements it.
The domain never imports zeep.

**Why.**
- Tests can replace the SOAP layer with a small in-memory fake, so they never depend on the
  live service.
- If the upstream became a REST API or a database, only `app/soap/` would change.
- Each file has one reason to change, which makes it easy to explain.

**Trade-off.** It takes more files than putting everything in the route handler. For a service
this size it is a little more structure than strictly necessary, but it shows the pattern a real
ERP integration needs.

## 5. Local copy of the WSDL

**Decision.** `app/soap/wsdl/CountryInfoService.wsdl` is committed to the repo, and zeep loads it
from disk. The endpoint address comes from configuration, not from the WSDL.

**Why.**
- Starting the app doesn't need the network, so the app still boots when upstream is down
  (`/health` keeps working, and requests get a clean 503 instead of a crash).
- Tests are deterministic and fast.
- The contract is versioned. If upstream changes its WSDL, that shows up as a reviewed diff
  instead of a surprise in production.

**Trade-off.** If upstream changes its contract, the local copy has to be refreshed by hand.
For a contract-first protocol like SOAP, that is usually what you want.

## 6. Upstream quirks are handled in the gateway

The live service has behaviour a naive client would get wrong. All of it is handled in
`app/soap/gateway.py`, so the rest of the app sees clean data:

| Quirk | Handling |
|---|---|
| An unknown country code is **not** a SOAP Fault. It returns a normal response named "Country not found in the database" with an empty code. | The gateway checks for the empty code and raises the domain error `NotFound` (→ 404 later). It checks the empty code rather than the message text, which is more robust. |
| Codes are case-sensitive (`it` is "not found", `IT` works). | Codes are trimmed and uppercased before the call. |
| An unknown currency returns nothing (`None`) instead of an empty list. | Converted to `[]`. |
| The currency list contains a placeholder "No Universal Currency" with no code. | Skipped. |
| Empty strings for missing fields. | Converted to `null` in JSON. |

**Why the gateway and not the business layer?** These are facts about *this* upstream, not
business rules. If the provider changed, the quirks would change with it, so they belong in the
adapter for that provider.

Some upstream data is wrong (for example a "Ocenania" typo in a continent name). It is passed
through unchanged: a gateway should not quietly rewrite its source's data.

## 7. Error translation: technical errors become domain errors

**Decision.** `app/domain/errors.py` defines a small hierarchy, and the gateway converts every
failure into one of these errors:

| Upstream problem | Domain error | Stable `code` |
|---|---|---|
| unknown entity | `NotFound` | `not_found` |
| SOAP Fault | `UpstreamFault` | `upstream_fault` |
| timeout (after retries) | `UpstreamTimeout` | `upstream_timeout` |
| cannot connect (DNS, refused) | `UpstreamUnavailable` | `upstream_unavailable` |
| HTTP error / unparseable XML | `UpstreamBadResponse` | `upstream_bad_response` |

The API layer (phase 2) maps these to HTTP status codes in one place. The SOAP code never
mentions HTTP statuses, and the routes never mention zeep.

## 8. Timeouts and retries

**Decision.**
- Every SOAP call has a timeout (`SOAP_TIMEOUT_SECONDS`, default 5s). Without one, a hung
  upstream would block worker threads forever.
- Transient failures are retried up to `SOAP_MAX_RETRIES` times (default 2), using **tenacity**
  with exponential backoff plus random jitter. Jitter stops many clients from retrying at the
  same instant.
- **Only transient failures are retried:** timeouts, connection errors, and HTTP 502/503/504.
  A SOAP Fault is the server deliberately refusing, so a retry would give the same answer.
- Retrying is safe because every operation is a read (idempotent).

**Trade-off.** In the worst case a request waits for `(retries + 1) × timeout` plus backoff
(around 15s with the defaults). The timeout applies to the connect and read phases
separately, so it is not a strict end-to-end deadline. For a public read-only API that is
fine. A stricter service would add an overall deadline.

## 9. Configuration through environment variables

**Decision.** `app/config.py` defines a `pydantic-settings` class. Every setting comes from
environment variables (or a local `.env` file that git ignores). Values are type-checked at
startup, so a typo such as `SOAP_TIMEOUT_SECONDS=abc` fails immediately instead of at the first
request. `.env.example` documents every variable, and secrets are never committed.

This follows the twelve-factor app approach: the same image runs in every environment, and
only the environment changes.

## 10. Structured JSON logging with the standard library

**Decision.** `app/logging_config.py` adds a roughly 30-line JSON formatter on top of Python's
`logging`. Every line is one JSON object, and extra fields (`operation`, `duration_ms`, `attempts`,
...) become searchable keys in tools like Loki, Datadog or CloudWatch.

**Alternatives.** `structlog` or `python-json-logger` are good libraries, but the standard
library is enough here and leaves one less dependency to explain.

---

# Phase 2: REST endpoints

## 11. URL design and versioning

**Decision.** Plural nouns for resources and nesting only where there is a real relationship.
Every endpoint is a `GET` because the upstream is read-only:

```
GET /v1/countries            ?continent=EU&currency=EUR&q=land&limit=50&offset=0
GET /v1/countries/{code}
GET /v1/continents
GET /v1/currencies
GET /v1/currencies/{code}/countries
GET /health   GET /health/ready
```

The `/v1` prefix in the URL means a future breaking change can live at `/v2` while existing
clients keep working. It's the most visible versioning style and the easiest to curl. Health
probes are not versioned: they're for infrastructure, not API clients.

**Alternatives.** Versioning through a header (`Accept: application/vnd...+json;version=1`) keeps
URLs cleaner, but it's harder to try in a browser and easy to forget.

## 12. One pagination envelope for every list

**Decision.** Every collection returns the same shape:
`{"items": [...], "total": 246, "limit": 50, "offset": 0}`. Pagination uses `limit`/`offset`
(max 250).

**Why.**
- One shape everywhere means clients write one pagination helper. Even the 6 continents use it,
  so adding fields later doesn't break anyone.
- `total` lets a UI show "page 2 of 5".
- Offset pagination is simple and fits small, stable data that is fetched in full.

**How.** The upstream has no filtering or paging. `FullCountryInfoAllCountries` returns all 246
countries in one call, so `app/domain/service.py` filters, sorts (by name or code, so pages are
stable) and slices the list in memory. Phase 3 caches that full list, so a page request rarely
reaches upstream at all.

**Trade-off.** With millions of rows or data that changes often, offset paging gets slow and can
skip or repeat items. Cursor (keyset) pagination would be the right tool then.

## 13. Domain models double as response schemas

**Decision.** Routes return domain models (`Country`, `Page[Country]`) directly, and FastAPI
generates the OpenAPI schema from them. `app/api/schemas.py` only holds API-specific shapes
(errors and health).

**Trade-off.** A change to a domain model changes the public API. That's fine at this size, where
they are the same thing. In a bigger system, separate API DTOs would let the two evolve
independently, at the cost of mapping code.

## 14. Errors: RFC 9457 Problem Details

**Decision.** Every error, whether from a domain error, a validation error, an unknown route or a
bug, returns `application/problem+json`:

```json
{"type": "about:blank", "title": "Not Found", "status": 404,
 "detail": "Country 'ZZ' was not found", "instance": "/v1/countries/ZZ",
 "code": "not_found", "request_id": "9f1c..."}
```

- `code` is a stable machine-readable string (`not_found`, `validation_error`,
  `upstream_timeout`, ...). Clients should branch on `code`, never parse `detail`.
- `request_id` lets a user report "it failed" and lets us find the exact log lines.
- Validation errors (422) add an `errors` list showing which field failed and why.

**Status code mapping** (one table in `app/api/errors.py`):

| Situation | Status | Why this status |
|---|---|---|
| bad input (`/countries/ITA`, `limit=0`) | 422 | FastAPI/Pydantic convention for a well-formed request with invalid values |
| unknown country / currency | 404 | the resource doesn't exist |
| SOAP Fault, unparseable response | 502 Bad Gateway | upstream answered, but wrongly |
| upstream unreachable | 503 Service Unavailable | temporary, retry later |
| upstream too slow | 504 Gateway Timeout | the standard status for exactly this case |
| bug in our code | 500 | generic body; the traceback is only logged |

**Not leaking internals.** For upstream and unexpected errors, clients get a generic sentence.
The real detail (SOAP operation name, fault text, traceback) goes to the logs under the same
`request_id`. Internal details are useless to clients and can help an attacker.

**Known limitation.** FastAPI lists the error schema under both `application/problem+json` and
`application/json` in the OpenAPI document. Real responses always use
`application/problem+json`.

## 15. Validate at the edge, normalise for the user

**Decision.** Path and query parameters have strict patterns (`^[A-Za-z]{2}$` for countries,
`^[A-Za-z]{3}$` for currencies, `limit` 1–250). Bad input is rejected with 422 before any
upstream call. Matching is case-insensitive: `/v1/countries/it` works, because the gateway
uppercases codes.

**Why.** Rejecting early avoids pointless upstream calls and gives clearer errors. Accepting
any case is friendlier and costs nothing.

## 16. Request IDs and access logging

**Decision.** `app/api/middleware.py`:
- reuses the caller's `X-Request-ID` if it is safe (max 128 chars of `[A-Za-z0-9._-]`), otherwise
  generates one, and returns it as a response header;
- stores it in a `ContextVar`, which the logging filter adds to *every* log line (SOAP calls,
  retries, errors), not just the access log;
- writes one access-log line per request: method, path, status, duration, client;
- is the last safety net: an unexpected exception is logged with its traceback and turned into a
  generic 500 problem response.

**Why validate the incoming id?** It ends up in logs and headers. Accepting arbitrary text
would allow log injection (fake log lines) or oversized headers.

## 17. Synchronous routes (`def`, not `async def`)

**Context.** zeep's normal client is synchronous (blocking). Calling blocking code inside an
`async def` route would freeze the whole event loop for every user.

**Decision.** Routes are plain `def`. FastAPI automatically runs them in a thread pool, so a slow
SOAP call blocks only its own thread. Context variables are copied into that thread, which is
why the SOAP log lines still carry the request id.

**Alternatives.** zeep has an `AsyncClient` (built on httpx). That would scale better under very
high concurrency, but it makes the code, retries and tests more complex. The thread pool
(40 threads by default) is more than enough for this service.

## 18. Liveness vs readiness

**Decision.** Two probes:
- `GET /health` (liveness) answers "is the process alive?" and never calls upstream. If upstream
  is down, restarting our container would not help, so liveness must stay green.
- `GET /health/ready` (readiness) answers "can we serve requests?" by making one cheap SOAP call,
  using its own gateway with a 2s timeout (`HEALTH_CHECK_TIMEOUT_SECONDS`) and no retries, so a
  probe never hangs. It returns 200 `ready` or 503 `unavailable` with the error code.

Both are public (no API key): load balancers and orchestrators must be able to call them.

## 19. Application factory

**Decision.** `create_app(settings=None, gateway=None)` in `app/main.py` builds the app instead of
creating it at import time. Run it with `uvicorn app.main:create_app --factory`.

**Why.** Tests can build an app with their own settings and a fake gateway, with no
monkey-patching and no global state shared between tests.
