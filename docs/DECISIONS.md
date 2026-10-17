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

---

# Phase 3: authentication, rate limiting, caching

## 20. API-key authentication

**Decision.** Clients send a key in the `X-API-Key` header. Valid keys come from the `API_KEYS`
environment variable (comma-separated). No key is stored in code or in the repo, and the app
**refuses to start** if `API_KEYS` is empty: an app with no keys is either open to everyone or
unusable, and both are mistakes worth catching at startup.

**Details worth knowing.**
- Keys are compared with `hmac.compare_digest`, against every configured key with no early exit.
  A plain `==` returns faster the sooner two strings differ, which in theory lets an attacker guess
  a key character by character from response times.
- A missing or wrong key gives `401` with `WWW-Authenticate: ApiKey` and the usual problem+json
  body. The two cases look identical, so a client can't tell "no such key" from "typo".
- Logs and rate-limit buckets use a **key id**: the first 12 hex characters of the key's SHA-256.
  It identifies a client in the logs without the log ever containing the key.
- The check is a FastAPI dependency on the `/v1` router, so a new route is protected by default.
  `/health`, `/docs` and `/openapi.json` are public on purpose (probes and documentation).
  The OpenAPI document declares the `X-API-Key` scheme, so the docs page has an "Authorize" button.

**Alternatives.** `Authorization: Bearer` is for tokens that identify a user and expire (OAuth/JWT).
Simple static keys for machine clients are normally sent in a custom header. A real deployment
would store hashed keys in a database with per-key metadata and revocation; environment
variables are the simplest thing that is safe for a demo.

**Known limitation.** Rotating a key means changing the environment and restarting.

## 21. Rate limiting: token bucket per key

**Decision.** `app/infra/ratelimit.py`. Each key has a bucket of `RATE_LIMIT_BURST` tokens (default
20) that refills at `RATE_LIMIT_PER_MINUTE` (default 60) per minute. A request costs one token.
When the bucket is empty the answer is `429 Too Many Requests` with a `Retry-After` header. Every
successful response carries `X-RateLimit-Limit` and `X-RateLimit-Remaining`.

**Why a token bucket.** It allows short bursts (a page that fires 10 calls at once) while still
enforcing the average rate. A fixed window ("60 per calendar minute") lets a client send 60 requests
at 0:59 and 60 more at 1:00.

**Details.**
- Rate limiting runs *after* authentication, so each bucket belongs to a known key. Unauthenticated
  requests are rejected cheaply with 401 and are not limited per key; a production system would
  also limit by IP at the proxy.
- The clock is injected, so tests can move time forward instead of sleeping.
- The limiter hides behind a `RateLimiter` Protocol with one method.

**Known limitation.** State is **in memory and per process**. With 4 workers, a client could
get up to 4× the limit, and a restart resets all buckets. For a multi-worker deployment the bucket
moves to Redis (atomic Lua script or `INCR` + `EXPIRE`); only the class behind the Protocol changes.

## 22. Response cache: cache-aside, wrapped around the gateway

**Decision.** `CachedCountryInfoGateway` (`app/infra/cached_gateway.py`) wraps the gateway port.
For each call it looks in the cache first, and on a miss calls upstream and stores the result for
`CACHE_TTL_SECONDS` (default 300; `0` disables caching). This is the **cache-aside** pattern.
The business service doesn't know the cache exists.

**Why cache raw upstream data, not final responses?**
- The country list is the same for every filter and page. `/v1/countries?continent=EU`,
  `?q=land` and page 2 all reuse one cached upstream call (checked: one `MISS`, then `HIT`s).
  Caching whole responses would create one entry per query string and mostly miss.
- Country data changes very rarely, so five minutes is cheap and safe. It also protects a
  slow sample service from our traffic.

**Details.**
- Cache keys are shared by all API keys. The data is public and identical for everyone; there is
  nothing per-user in it.
- Errors are never cached, so one failed call doesn't make the failure last for the TTL.
- The cache holds frozen models. Lists are copied on the way out so a caller can't change what
  is stored.
- The cache is bounded (`CACHE_MAX_ENTRIES`, default 1024): when full, expired entries are
  removed first, then the oldest one.
- Everything is guarded by a lock, because requests run in several threads (see entry 17).

**Known limitations.**
- In-memory and per process, like the rate limiter; the `Cache` Protocol (get/set/clear) is the
  seam for Redis.
- No request coalescing: if 10 requests miss at the same moment, 10 upstream calls happen
  ("thundering herd"). With a 5 minute TTL and a tiny dataset this is acceptable.

## 23. Cache bypass and visibility

**Decision.** A client sends the standard request header `Cache-Control: no-cache` to skip the
cache. The result is also written back, so the next normal request sees fresh data. Every cached
route replies with `X-Cache: HIT`, `MISS` or `BYPASS`.

**Why standard HTTP instead of a custom header.** `no-cache` already means "revalidate before
using a stored copy", so clients, browsers and proxies understand it. It needs no documentation
beyond one sentence.

**How it works.** The middleware creates a small `CacheTrace` object per request and stores it
in a `ContextVar`; the cached gateway updates it. A mutable object is used because routes run in
a worker thread, and a `ContextVar.set()` made inside that thread would not be visible to the
middleware afterwards, whereas changes to a shared object are. The same trace feeds the access
log (`cache`, `key_id` fields), so one log line shows who called, and whether the cache helped.

**Note.** Bypass is available to any valid key. It is rate limited like any request, which caps
how much extra load a client can put on the upstream service.

---

# Phase 4: tests and CI

## 24. Test layout: three kinds of tests, two seams

| Folder | What it tests | What is fake |
|---|---|---|
| `tests/unit/` | one class or function at a time: cache, rate limiter, service, SOAP gateway, config, logging | nothing, or only the lowest layer |
| `tests/api/` | the whole HTTP app through `TestClient`: auth, rate limits, cache headers, errors, status codes | the SOAP gateway (`FakeGateway`) |
| `tests/integration/` | the real SOAP service | nothing (marked `integration`, off by default) |

There are **two places where the SOAP layer is replaced**, on purpose:

1. **`FakeGateway`** (`tests/fakes.py`) implements the `CountryInfoGateway` port with canned data.
   API tests use it, so they exercise routing, validation, auth, caching and error mapping
   without any SOAP or XML. This is what the port from entry 4 buys us.
2. **`responses` + recorded XML** (`tests/fixtures/soap/*.xml`) fakes the *HTTP layer under zeep*.
   The SOAP gateway tests therefore run the real zeep, the real WSDL, the real XML parsing, the
   real field mapping and the real retry logic. Only the network is fake. Mocking zeep itself
   would test our assumptions about zeep, not the code.

`responses` raises an error for any request without a registered reply, so a test that
accidentally reached the internet would fail loudly instead of silently depending on it.

## 25. Time is injected, not slept

The cache and the rate limiter take a `clock` function. Tests pass a `FakeClock` and call
`advance(61)` to expire a TTL or refill a bucket. The suite runs in a few seconds, never
flakes because of timing, and the retry tests use `backoff_seconds=0` for the same reason.

## 26. What the tests are meant to prove

They are written around behaviour that matters, not around lines of code:
- **Contract:** status codes, problem+json shape, headers, the pagination envelope, OpenAPI content.
- **Security:** every `/v1` route rejects missing, wrong, empty and almost-right keys; unauthenticated
  and rate-limited requests never reach upstream; the raw key never appears in logs; internal error
  details never reach clients.
- **Resilience:** which failures are retried (timeouts, connection errors, 502/503/504) and which
  are not (SOAP faults, 4xx), how many attempts are made, and what each failure becomes.
- **Upstream quirks:** the "not found is a normal response" case, lower-case codes, the codeless
  currency, `None` instead of an empty list, empty strings becoming `null`.
- **Cache:** hit, miss, bypass, TTL expiry, errors not cached, entries shared across filters and keys.

Coverage is a smoke alarm, not a goal: it stands at 100% (statements and branches), CI fails
below 90%, and the number only means something because of the checks above.

**Mutation check.** To make sure the tests can fail, I deliberately broke the code in seven ways
(timeout mapped to 500, SOAP faults retried, cache that never stores, auth that accepts any key,
limiter that never refuses, codes not upper-cased, bypass ignored) and confirmed that each
one made the suite fail. This was a one-off manual exercise, not an automated tool.

## 27. Integration tests stay out of the default run

`pytest` deselects tests marked `integration` (`addopts = "-m 'not integration'"` in
`pyproject.toml`); `pytest -m integration` runs only those. They call the real service, so a failure
can mean "upstream is down" rather than "our code is broken", which is the wrong signal for a
commit check. In CI they only run when started manually.

One integration test compares the committed WSDL with the live one (ignoring whitespace). If
the provider changes its contract, that test fails and shows exactly what changed, which is
the maintenance cost of entry 5 made visible.

## 28. CI pipeline (`.github/workflows/ci.yml`)

- **Triggers:** every push and pull request, plus a manual run (with an option for the
  integration tests).
- **`lint` job:** `ruff check` and `ruff format --check`. Linting is its own job so a style error
  shows up in seconds and is clearly separate from a test failure.
- **`test` job:** a matrix over Python 3.11, 3.12 and 3.13 (the supported range in `pyproject.toml`)
  with `fail-fast: false`, so one failing version doesn't hide the others. Runs with `-W error`, so
  a deprecation warning fails the build instead of piling up.
- **Least privilege:** `permissions: contents: read`; the jobs can't write to the repository.
- **`concurrency`:** a newer push cancels the older run on the same branch.
- **pip cache** keyed on `pyproject.toml`, which has no lock file (see below).
- The workflow was checked with `actionlint`, and the suite was run locally on 3.11, 3.12 and
  3.13 in clean environments. The workflow itself has not run on GitHub yet.

**Known limitations.** There is no lock file, so CI installs the newest versions allowed by the
ranges in `pyproject.toml`. That finds breakage from new releases early, but a build is not
bit-for-bit reproducible. A lock file (for example `pip-compile` or `uv.lock`) would fix that.
There are no type checks (mypy/pyright) and no dependency or vulnerability scan yet.

## 29. Things the tests found along the way

- tenacity deprecated the `initial` argument I used for backoff; the `-W error` setting turned the
  warning into a failure. The code now uses `multiplier`, and `pyproject.toml` requires tenacity
  9.2 or newer.
- Starlette's `TestClient` now prefers the `httpx2` package, so the dev dependency changed from
  `httpx` to `httpx2`.

---

# Phase 5: Docker and documentation

## 30. Multi-stage Dockerfile

**Decision.** Two stages. The *build* stage installs the app into a virtualenv (`/opt/venv`); the
*runtime* stage starts from a fresh `python:3.12-slim` and copies only that virtualenv.

**Why.** The final image has no build leftovers, no pip cache and no source tree, only the installed
package. The app is installed as a normal (non-editable) package, so the WSDL file must ship
inside it: `pyproject.toml` declares it as package data, and I checked that it ends up in the
installed package. `COPY pyproject.toml README.md` happens before `COPY app`, so rebuilding after a
code change doesn't redo the dependency layer's inputs unnecessarily.

**Trade-off.** The image is about 280 MB, mostly because of `lxml` (required by zeep). A distroless
or Alpine base would be smaller, but Alpine's musl libc makes compiled wheels harder, and the
extra effort isn't worth it here.

## 31. Container hardening

- **Non-root user** (uid 10001). A bug in the app does not become root inside the container.
- **`read_only: true`**: the root filesystem is read-only. The app writes nothing to disk (logs
  go to stdout, caches live in memory, bytecode writing is disabled). I tested that the service
  starts and serves requests in this mode.
- **`cap_drop: ALL`** and **`no-new-privileges`**: no Linux capabilities are needed to listen on
  port 8000, and the process can't gain new ones.
- **`.dockerignore`** keeps `.env`, `.git`, tests and docs out of the build context, so a
  local secret can't end up in an image layer (checked: the image contains no `.env`).

## 32. Health check without curl

`python:slim` has no `curl`, and installing it only for a probe adds attack surface. The
`HEALTHCHECK` runs a one-line Python `urllib` call against `/health` instead. It uses the
liveness endpoint, not readiness, for the reason in entry 18: an upstream outage must not make
the container look broken and get restarted. `docker compose ps` shows `healthy` once it passes.

## 33. One worker process

The `CMD` runs a single uvicorn process. The cache and the rate limiter live in process memory
(entries 21 and 22), so several workers would each have their own copy: the rate limit would be
multiplied by the worker count and the cache hit rate would drop. Concurrency inside the process
comes from FastAPI's thread pool (entry 17). Scaling out needs the shared store described in
those entries.

## 34. Configuration and secrets in Compose

`docker-compose.yml` reads `.env` through `env_file`. Nothing secret is in the image or in the
repository. If `.env` is missing, Compose stops with a clear "env file not found" error.

`.env.example` is public, so any key written in it is known to everyone. To stop people from
deploying it by accident, **the app refuses to start if a key begins with `change-me`**
(`create_app` in `app/main.py`, covered by a test). The README quick start therefore generates a
random key with `openssl rand -hex 24` and writes only `API_KEYS` to `.env`; every other setting
has a safe default.

## 35. The README

It is written for a reader who has two minutes: what it is, one command to run it, a diagram of
the request flow, real curl examples, then the details. The curl examples and error bodies
were copied from a running container, not written by hand. The Mermaid diagram was parsed with
Mermaid's own parser (and a deliberately broken diagram was confirmed to be rejected), so GitHub
should render it.

## 36. What was verified, and what was not

Verified on a real Docker 29.9.1 daemon: the image builds; `docker compose up` reaches `healthy`;
the process runs as uid 10001; the API works through the container, with the read-only filesystem
and dropped capabilities; the placeholder key is refused; logs are JSON on stdout; the CI
smoke-test script passes when run by hand.

Not verified: the GitHub Actions workflow has never run on GitHub (no remote yet). It was checked
with `actionlint`, and its jobs' commands were run locally.
