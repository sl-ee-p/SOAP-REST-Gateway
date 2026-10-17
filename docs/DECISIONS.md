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
