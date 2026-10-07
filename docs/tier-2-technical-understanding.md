# NearHome Tier 2 Technical Understanding

## 0. What Tier 2 is about

Tier 1 answers:

> How does NearHome work?

Tier 2 answers:

> How does NearHome continue working correctly when real-world systems are slow, incomplete, expensive, inconsistent or broken?

This is a learning guide, not a catalogue of framework features. It is based on the current repository as audited on 2026-08-30. The executable Python and TypeScript code is treated as the primary source of truth. Product documents are useful context, but a document cannot make a feature real if the code does not implement it.

NearHome's happy path is straightforward:

```text
User confirms an HDB listing
        ↓
NearHome geocodes the address with OneMap
        ↓
NearHome calculates transport, driving, school and price evidence
        ↓
NearHome shows a comparison and recommendation
```

The engineering questions begin when the world is less cooperative:

```text
User submits HDB listing
        ↓
NearHome calls OneMap
        ↓
What if OneMap:
- times out?
- returns no result?
- gives the wrong coordinates?
- rate-limits NearHome?
- returns unexpected JSON?
```

Tier 2 is the set of mechanisms around the core feature that make the answer dependable: validation, provenance, timeouts, bounded work, caches, queues, workers, fallbacks, durable status, tests, logs and operational checks.

One important NearHome design principle is:

```text
Raw provider data
        ↓
Validation and normalisation
        ↓
Canonical NearHome data
        ↓
Deterministic calculation
        ↓
Stored evidence and explanation
        ↓
UI result
```

This keeps a provider's answer separate from a NearHome-derived metric. For example, Google may provide a route duration; NearHome derives a transport or driving score from that route. The score is not an official Google score.

---

# 1. DATA QUALITY

Data quality means deciding whether data is safe enough to use. A response can be syntactically valid—valid JSON, valid numbers, and the expected keys—and still be semantically wrong for NearHome.

For example:

```python
latitude = 51.5074
longitude = -0.1278
```

Those are valid coordinates, but they are London, not Singapore. Data quality checks ask whether a value makes sense in NearHome's domain, not merely whether a parser can read it.

## 1.1 Input validation at the API boundary

The main API contracts are Pydantic models in `apps/api/app/schemas/comparison.py`.

Examples:

* `BuyerProfileInput.max_budget` must be greater than zero.
* `BuyerProfileInput.priorities` must contain one to three items.
* `BuyerProfileInput.named_schools` is limited to ten entries.
* `ManualListingInput.asking_price` and `floor_area_sqm` must be greater than zero.
* `ManualListingInput.address` must be at least three characters.
* `ManualListingInput.flat_type` must be at least two characters.
* `renovation_estimate` cannot be negative.
* `remaining_lease_years`, when supplied, must be greater than zero.
* Enums such as `PriorityType`, `MainTransportMode`, `DayType` and `PropertyCategory` reject arbitrary strings.

This is boundary validation: bad request shapes are rejected before the service layer is asked to calculate anything. FastAPI turns Pydantic failures into a 422 response.

There is an important limitation: the manual-listing schema checks positivity, but it does not enforce every domain-plausibility range. A manually entered floor area of 1,000 square metres is positive and therefore passes this schema. Smart Paste has additional plausibility warnings, but manual entry is not equally strict. That is a genuine gap, not something to silently assume away.

`BuyerProfileInput.normalize_named_schools()` also performs normalisation. It trims whitespace, compares names case-insensitively, removes duplicates, and keeps the compatibility field `named_school` aligned with the first item. `validate_priority_count()` rejects the same priority more than once.

## 1.2 Smart Paste has several validation layers

Smart Paste is not treated as trusted because an LLM produced JSON. The path is:

```text
Browser request
  → SmartPasteInput
  → retrieval / text preparation
  → LLM structured response
  → SmartPasteExtractionSchema
  → deterministic candidate validation
  → candidate reconciliation
  → user review and confirmation
  → ConfirmedListing
```

Relevant code:

* `apps/api/app/schemas/comparison.py -> SmartPasteInput`
* `apps/api/app/schemas/smart_paste.py -> SmartPasteExtractionSchema`
* `apps/api/app/adapters/live/groq.py -> LiveGroqAdapter.extract()`
* `apps/api/app/services/smart_paste/preparation.py -> prepare_paste_text()`
* `apps/api/app/services/smart_paste/validation.py -> validate_extraction_candidates()`
* `apps/api/app/services/smart_paste/reconciliation.py -> reconcile_candidates()`
* `apps/api/app/services/smart_paste/service.py -> SmartPasteService`

`SmartPasteExtractionSchema` uses `extra="forbid"`. This means an unexpected top-level field is not quietly accepted. Each evidence field includes its value, raw text, source snippet, source section, model confidence and status. Missing facts are represented as `null` or a status such as `NOT_FOUND_IN_SOURCE_TEXT`, rather than guessed values.

`LiveGroqAdapter` also puts a limit of 15,000 characters on content sent to Groq. Its prompt explicitly says not to extract from recommended listings, mortgage calculators, agent profiles or nearby projects. It says not to infer storey from a unit number, and not to treat “99-year Leasehold” as the remaining lease.

`validate_extraction_candidates()` then applies deterministic checks. For example:

```python
if field_name == "asking_price":
    if v <= 0:
        warnings.append(...)
        continue
    if v < 50000:
        warnings.append(...)

if field_name == "floor_area_sqm":
    if v < 20 or v > 200:
        warnings.append(...)
```

The first condition rejects the candidate from the validated set. The second condition records a warning but does not necessarily discard the candidate. That is a useful distinction between “cannot use this value” and “this value is unusual and needs review.”

`reconcile_candidates()` chooses the highest-confidence accepted candidate, records all candidate evidence, and emits a warning when different candidates disagree. This is not the same as proving that the selected candidate is correct. It is a controlled suggestion presented for user confirmation.

## 1.3 Address normalisation and exact HDB identity

There are two related but different address helpers:

* `apps/api/app/core/utils.py -> normalize_address()` uppercases and collapses whitespace for duplicate-listing checks.
* `apps/api/app/utils/hdb_address.py -> normalize_hdb_address()` extracts a canonical block/street identity for HDB matching.

`normalize_hdb_address()` uses Unicode normalisation, removes common `BLK`/`BLOCK` prefixes, removes embedded six-digit postal codes from the street identity, expands suffixes such as `AVE` to `AVENUE`, and removes a trailing `SINGAPORE` token. It returns `(block, street, postal_code)`. `canonical_hdb_address_key()` reduces that to `(block, canonical_street)`.

This is normalisation, not fuzzy matching. The module deliberately requires an exact canonical block-and-street key. It avoids a dangerous result such as treating every transaction on a similarly named road as evidence for the listing.

The confirmed-listing repository stores the normalised key in `confirmed_listings.normalized_address_key`. The database also has a unique constraint on `(session_id, normalized_address_key, asking_price)`, and `SessionRepository.has_duplicate_listing()` checks the same identity before insertion. The route still catches a database `IntegrityError`, because two requests could pass the pre-check at almost the same time.

## 1.4 Geocoding quality checks

The live OneMap adapter is `apps/api/app/adapters/live/onemap.py -> LiveOneMapAdapter`.

OneMap results are checked in `_is_usable_match()`:

```python
if not (
    1.15 <= latitude <= 1.49
    and 103.58 <= longitude <= 104.12
):
    return False

requested = canonical_hdb_address_key(original_address)
returned = canonical_hdb_parts(result.get("BLK_NO"), result.get("ROAD_NAME"))
return requested is None or returned is None or requested == returned
```

This protects against two different problems:

1. A coordinate/order/provider error that produces a location outside the broad Singapore envelope.
2. A ranked search result that is technically a real place but belongs to a different block or street.

Missing provider block/street fields are allowed when the coordinate itself is valid. The code comments explain why: some legitimate OneMap rows do not expose all address components. This is defensive validation, not address normalisation. Normalisation makes equivalent representations agree; validation decides whether a result is acceptable.

`LiveOneMapAdapter.geocode()` tries a small set of query variants: the original address, a version without a leading block prefix, and a version with `, Singapore` when needed. Those are query variants, not a retry policy. The code does not have a general HTTP retry loop for OneMap.

The browser's important-location search uses a separate server-side Next.js route, `apps/web/src/app/api/geocode/route.ts`. Because that process cannot import the Python Pydantic models, it defines equivalent Zod contracts for the OneMap token envelope, search envelope and consumed result fields. It validates a 2–200 character query, converts string coordinates to finite numbers, skips malformed individual suggestions while preserving valid ones, returns at most eight suggestions, and refreshes a cached token once after a 401 or 403. A malformed top-level response becomes a controlled upstream-provider failure rather than an empty successful result.

## 1.5 External-response validation

NearHome now applies the same two-stage idea at external-provider boundaries that it already used for frontend input and Smart Paste output:

```text
raw provider JSON or normalized snapshot
        ↓
provider-specific Pydantic structural validation
        ↓
NearHome semantic and cross-record validation
        ↓
provider-independent dataclass used by engines
```

The provider models live in `apps/api/app/adapters/provider_schemas/`, separately from the public API models in `apps/api/app/schemas/`. `ProviderPayload` uses `extra="ignore"`: Google, OneMap or data.gov.sg may add fields without breaking NearHome, but every field NearHome consumes must still have the expected nested shape and type. A type annotation such as `dict[str, Any]` does not perform this runtime check; Pydantic does.

Structural validation does not replace application validation. A coordinate can be a valid finite number but still be outside Singapore. An LTA row can contain correctly typed fields but refer to an unknown bus stop. The Pydantic layer answers “can NearHome safely parse this?”; the existing semantic and dataset-quality layers answer “should NearHome trust or use this?”

### OneMap and Google Places

`apps/api/app/adapters/provider_schemas/onemap.py` defines the OneMap token, search-envelope and search-result contracts. `LiveOneMapAdapter` validates the raw token/search JSON before reading it. It then retains the existing Singapore coordinate bounds and canonical HDB block/street match as semantic checks. A malformed envelope or missing/invalid consumed coordinate cannot leak into `GeocodeResult`.

`apps/web/src/app/api/geocode/route.ts` is a second OneMap ingestion boundary in the TypeScript process. Its matching Zod models provide the same structural guarantee there; Pydantic remains the Python backend's provider-validation mechanism.

`apps/api/app/adapters/provider_schemas/google_places.py` mirrors only the nested Places fields NearHome consumes: autocomplete `suggestions[].placePrediction` and Place Details `id`, `formattedAddress` and `location`. Non-place suggestion variants can be ignored, provider-added fields are tolerated, and a malformed place prediction/details response becomes `AdapterError(error_code="invalid_output")` rather than an incidental `KeyError` or `AttributeError`.

### Google Routes

`apps/api/app/adapters/provider_schemas/google_routes.py` validates the complete consumed subset of Compute Routes and Compute Route Matrix responses, including nested legs, steps, navigation instructions, transit details and polylines. It accepts Google's documented duration string and the object form already supported by NearHome, converts either to finite non-negative seconds, and validates indices and distances.

`apps/api/app/adapters/routing/google.py -> GoogleRoutingProvider` applies `GoogleRoutesResponse` before constructing `RouteResult`:

* the top-level response must be an object containing a route list;
* every consumed route, leg and step must have the expected structure;
* route duration must exist and parse as finite non-negative seconds;
* `distanceMeters` must exist, parse as a non-negative integer;
* an empty `routes` list becomes `RoutingUnavailableError`;
* malformed JSON or a malformed success structure becomes `RoutingProviderError`, with malformed structure classified as `invalid_output`.

The reason for rejecting a missing duration is important. If the code interpreted a missing duration as zero, a malformed provider response could win every comparison and be cached as a false success.

Both `GoogleRoutingProvider.get_route_matrix()` and the older `apps/api/app/adapters/live/google_routes.py -> LiveGoogleRoutesAdapter.route_matrix()` validate the raw top-level matrix array with `TypeAdapter(list[GoogleRouteMatrixRow])`. A non-list response or malformed row now becomes a safe `RoutingProviderError(error_code="invalid_output")`; it cannot be interpreted as a successful empty matrix or fabricate route times.

### HDB carpark data

`apps/api/app/adapters/provider_schemas/hdb.py` defines schemas for raw data.gov.sg carpark and resale-transaction rows, live HDB availability, static carpark rows and normalized transaction rows. The HDB ingestion scripts validate provider rows before normalisation and validate the resulting snapshot rows again before writing them. In particular, a resale row missing floor area, price or lease commencement now fails validation instead of receiving fabricated `90 sqm`, `$0` or `1990` defaults. `apps/api/app/adapters/parking/hdb_carpark.py -> HdbCarparkStore.load()` revalidates the complete static fixture before converting rows. Missing identity/address, malformed optional numbers, out-of-range/non-finite coordinates or malformed timestamps therefore fail at the maintenance boundary and cannot later pass silently at runtime. Valid carpark rows are still deduplicated by `carpark_no`, and the source carpark type is preserved while also being normalised into `BASEMENT`, `MULTI_STOREY`, `SURFACE` and related stable values.

`apps/api/app/adapters/parking/hdb_availability.py -> CarparkAvailabilityProvider.fetch()` validates the data.gov.sg envelope before parsing. Numeric strings are intentionally converted to non-negative integers, empty count fields become `None`, and `lots_available > total_lots` is rejected. `_parse_response()` then preserves missing and stale states, calculates an availability percentage only when total lots is positive and available lots is present, and labels records `LIVE`, `STALE` or `TIMESTAMP_UNAVAILABLE` rather than treating missing data as zero availability.

`FixtureHDBTransactionsAdapter` similarly validates every normalized resale row before creating `TransactionRecord`: IDs and identity fields are non-empty, transaction month has `YYYY-MM` structure, price/area/PPSM are positive, and lease values are non-negative. Comparable selection and fair-price consistency remain application-level behavior.

### LTA bus reference data

`apps/api/app/adapters/provider_schemas/reference.py` validates both raw LTA DataMall page envelopes and normalized LTA stop, route and service rows. The maintenance scripts reject malformed raw pages before transforming them, then validate normalized rows before publishing a snapshot. Runtime revalidates those rows before index construction. Required identifiers must exist, coordinates and distances must be finite and in range, route direction must be `1` or `2`, and stop sequence must be positive. The same module validates MRT, MOE school and rail station/edge snapshots before their dataclasses are built.

After row validation, `apps/api/app/adapters/transport_data/lta_bus.py -> LtaBusDataStore` still builds a `DataQualityReport`. Pydantic cannot establish relationships across separate files, so the report checks for empty datasets, the proportion of stops with matched routes, minimum service-direction count, unknown stop references, duplicate stop sequence numbers and malformed frequency values. If the joined dataset is below the configured usability thresholds, public-transport calculations can return `not_assessed` rather than silently scoring a broken network.

### Static road and model artifacts

`SlaMajorRoadFeatureCollection` validates the filtered SLA GeoJSON feature/geometry structure when it is produced and again when it is loaded. `MajorRoadMappingArtifact` in `apps/api/app/adapters/provider_schemas/artifacts.py` validates the persisted road schema version, metadata envelope, three-part OSM edge identifiers, entry nodes, finite coordinates and positive search radius. Both artifact writers validate the object they are about to publish. `apps/api/app/adapters/transport_data/major_road_network.py -> SlaOsmMajorRoadMappingStore` then performs semantic compatibility checks against the installed SLA and OSM file hashes, algorithm version and catalogue version. In live mode, missing or incompatible artifacts are a deployment/startup problem, not a reason to invent a request-time road match.

`FairPriceModelMetadata` validates the CatBoost metadata structure, finite numeric medians/residuals, non-empty feature/type lists, row-count bounds, month format and residual interval ordering. `apps/api/app/engines/fair_price_catboost.py -> load_artifact()` then checks model version, transaction snapshot key and exact feature compatibility before loading the model. An invalid artifact returns `None`; it does not retrain during an inference request.

Groq remains the earlier example of this pattern: `SmartPasteExtractionSchema.model_validate()` checks the structured LLM response before deterministic extraction validation and reconciliation.

## 1.6 Provenance, confidence and verification states

NearHome stores more than the value itself.

`apps/api/app/domain/enums.py` defines vocabularies such as:

* `DataStatus`: `AVAILABLE`, `UNAVAILABLE`, `EXTRACTION_UNCERTAIN`, `CONFLICTING_VALUES`, `TEMPORARILY_UNAVAILABLE`, `INSUFFICIENT_EVIDENCE` and others.
* `Provenance`: `USER_ENTERED`, `EXTRACTED_LLM`, `OFFICIAL`, `INFERRED`, `CALCULATED`, `MOCK_DEMO_DATA`, `ROUTED_LIVE`, `CURATED_REFERENCE_DATA` and `UNAVAILABLE`.
* `VerificationState`: used for claims that have not been independently verified.

The domain objects in `apps/api/app/domain/models.py` mirror this vocabulary. A `ConfirmedListing` has field provenance, source URL, source hash, lease source/confidence/status and raw listing attributes. An `EnrichedField` has a value, status, source, retrieval time, confidence, assumptions and provenance. Smart Paste keeps raw text, cleaned text, source snippets, extraction metadata, warnings and agent claims.

The database stores these fields in JSONB or typed columns. `EnrichedFieldORM` includes `status`, `source`, `source_version`, `retrieved_at`, `confidence`, `assumptions_json` and `provenance`. `ExtractionAttemptORM` stores content hash, pipeline version, model/prompt/schema versions and timestamps.

This is why NearHome can say “this is calculated from a live route” or “this is mock demo data” instead of presenting every number as equally authoritative.

## 1.7 Raw, canonical and derived data: one real path

The live geocoding path is:

```text
OneMap JSON
  ↓
LiveOneMapAdapter._is_usable_match()
  - parse latitude/longitude
  - enforce Singapore bounds
  - compare canonical HDB block/street when available
  ↓
GeocodeResult
  - latitude, longitude, formatted address, town, provider,
    provenance and retrieved_at
  ↓
EnrichmentService._enrich_listing()
  - saves enriched_fields.geocode
  - records an enrichment run status
  ↓
PostgreSQL JSONB evidence
  ↓
ComparisonService.get_comparison()
  - feeds coordinates to transport/driving/school engines
  ↓
Derived model rollups and UI explanations
```

The raw OneMap response is not the same thing as the `GeocodeResult`; provider-specific keys have been mapped to NearHome's canonical shape. The transport score is another layer again.

## 1.8 Missing data and conflicts

NearHome generally prefers an explicit unavailable state to a made-up number.

Examples:

* Missing coordinates cause public transport and driving stages to be marked unavailable.
* Missing HDB transaction evidence can make fair price `INSUFFICIENT_EVIDENCE`.
* Missing bus/rail evidence can produce a `not_assessed` component.
* A route-matrix element without a duration is stored as temporarily unavailable for that listing/destination.
* A missing carpark availability timestamp is labelled `TIMESTAMP_UNAVAILABLE`.
* A missing exact flat model causes flat-model similarity to be omitted; it is not guessed from floor area.

There is explicit conflict handling in Smart Paste: candidate conflicts are preserved and a warning is shown. HDB flat subtype/model logic also gives explicit listing evidence priority over conservative historical inference. For town derivation, `fair_price_comparables.py -> derive_town_match_evidence()` can identify an ambiguous result.

There is not one universal cross-provider conflict-resolution engine. For example, the code does not appear to merge a live OneMap town and a transaction-derived town using a configurable voting policy; it prefers the geocoder town when present and uses transaction matching when the geocoder does not provide a town. That is a specific rule, not a general conflict framework.

### Interview questions I should be able to answer

**How do you ensure third-party data is trustworthy?**

I validate it at several boundaries. Pydantic validates request shape, adapters validate provider responses, OneMap coordinates are constrained to a Singapore envelope and checked against canonical block/street identity, routing rejects malformed durations/distances, and the system stores source, retrieval time, confidence and provenance. If evidence is missing or ambiguous, the result is labelled unavailable or insufficient rather than silently fabricated.

**What is defensive validation?**

It is checking assumptions before unsafe data reaches downstream calculations. In NearHome, rejecting a valid-but-London coordinate for an HDB listing is defensive validation because it prevents a plausible but wrong location from contaminating all transport and school calculations.

**What is the difference between validation and normalisation?**

Normalisation converts equivalent inputs to a stable representation, such as `AVE` to `AVENUE`. Validation decides whether a value is acceptable, such as rejecting a coordinate outside Singapore. They are often used together but solve different problems.

**Why use canonical schemas?**

Provider payloads change shape and use provider-specific names. `GeocodeResult`, `RouteResult`, `RouteMatrixResponse` and the domain models give engines a stable contract. This means a calculation engine does not need to know whether the data came from Google, a mock or another future provider.

**What happens if different sources disagree?**

The repository uses field-specific rules rather than one universal policy. Smart Paste keeps conflicts and asks for confirmation; explicit flat-subtype evidence beats historical inference; OneMap town is preferred when present; and missing/ambiguous evidence is surfaced through status and warning fields. There is no general cross-provider conflict resolver.

---

# 2. ERROR AND FAILURE HANDLING

An error is not just an exception. Good failure handling answers four questions:

```text
What failed?
How was it detected?
What safe result can be returned?
What work can continue?
```

## 2.1 Failure categories in NearHome

| Failure category | NearHome example | Main boundary |
| --- | --- | --- |
| User/input failure | Invalid budget, missing pasted text, unsupported requirement | Pydantic schemas and route handlers |
| External API failure | OneMap 403, Google 429, Groq invalid response | Provider adapters |
| Network failure | HTTP timeout or transport error | `httpx`, Groq client, worker classification |
| Database failure | PostgreSQL unavailable during readiness or persistence | SQLAlchemy/repository/API middleware |
| Worker/queue failure | Cloud Tasks cannot enqueue or worker dies | Job repository, dispatcher, worker endpoint |
| Calculation/model failure | Invalid CatBoost artifact or insufficient comparables | Engines and model loader |
| Programming/internal failure | Unexpected exception in a route or engine | Middleware/worker exception boundary |
| Deployment/infrastructure failure | Missing live road artifact or missing production setting | Startup validation and deployment scripts |

## 2.2 HTTP and schema failures

FastAPI and Pydantic handle request shape errors before business logic. Routes add domain-specific HTTP responses:

* Missing sessions return 404.
* Duplicate listings return 409.
* Invalid hard requirements return 400.
* An unconfigured or failed Cloud Tasks dispatch is recorded as a failed job and exposed through a safe message.
* Smart Paste retrieval uses structured codes such as `LISTING_PAGE_UNAVAILABLE` and `GEOCODING_UNAVAILABLE`.

`RequestLoggingMiddleware` in `apps/api/app/core/logging.py` catches unhandled exceptions, logs `request_failed`, and returns a generic 500 body containing a request ID. It prevents raw exception details from becoming the public response.

This middleware does not replace route-level validation. It is the last safety boundary for exceptions that nobody handled more specifically.

## 2.3 Timeouts

A timeout is a maximum waiting period. Without one, a connection to a stalled provider can keep an API request or worker occupied indefinitely.

Actual values in this repository include:

* Live OneMap API client: `httpx.Client(timeout=15.0)` in `apps/api/app/adapters/live/onemap.py`.
* Live Google Places: 15 seconds in `apps/api/app/adapters/live/google_places.py`.
* Legacy Google route matrix: 30 seconds in `apps/api/app/adapters/live/google_routes.py`.
* Shared Google Routes provider: 20 seconds for `computeRoutes` and matrix POSTs in `apps/api/app/adapters/routing/google.py`.
* Groq client: 60 seconds in `apps/api/app/adapters/live/groq.py`.
* Browser retrieval: a 10-second request timeout in the Next.js geocode route; Smart Paste retrieval and Playwright have separate settings such as `PLAYWRIGHT_TIMEOUT_SECONDS=25`.
* Browser-to-API ordinary calls: 15 seconds in `apps/web/src/lib/api.ts`.
* Browser Smart Paste and inline enrichment calls: 120 seconds.
* Database connection timeout: five seconds; SQLAlchemy pool wait timeout: ten seconds in `apps/api/app/db/session.py`.
* Cloud Tasks dispatch deadline: configurable, default 600 seconds.
* A durable enrichment job is considered stale after `ENRICHMENT_JOB_STALE_SECONDS`, default 660 seconds.

The repository does not split every provider timeout into separately named connect, read and total timeout values. Where it uses `httpx.Client(timeout=...)`, the configured value is an overall httpx timeout setting rather than a NearHome-specific workflow timer.

## 2.4 Retries, query variants and fallbacks are different

These three ideas are easy to mix up:

* A **retry** repeats the same operation, usually because the failure might be temporary.
* A **query variant** asks the same provider a slightly different question.
* A **fallback** uses a different method or source.

NearHome uses all three, but not everywhere.

### Confirmed retry behavior

* `CarparkAvailabilityProvider.fetch()` makes up to two attempts for `httpx.HTTPError`.
* `LiveGroqAdapter.extract()` makes one schema-repair retry when the provider rejects the generated object for schema-generation reasons. It does not blindly retry every Groq error.
* The Cloud Tasks worker requeues selected transient job failures up to `MAX_ENRICHMENT_JOB_ATTEMPTS`, default three. Retryable categories are Python/HTTP timeouts, HTTP transport/connection errors, and `AdapterError` codes `timeout`, `temporarily_unavailable` or `rate_limit`.
* The data-ingestion script `data_pipeline/ingest_hdb_carparks.py` has its own bounded retry behavior for its live maintenance download.

There is no exponential-backoff implementation in the application code that I could find. Cloud Tasks may have platform delivery behavior, but NearHome's repository does not encode a custom exponential-backoff formula. It is inaccurate to say “NearHome retries with exponential backoff.”

OneMap has query variants but no general HTTP retry loop. Google adapters classify 429/5xx as retryable in their error objects, but the normal routing provider does not itself repeat the HTTP request. In many enrichment paths the provider error is converted into a component-level unavailable/provider-error result instead of reaching the worker retry boundary.

## 2.5 Failure boundaries and graceful degradation

The most important failure isolation lives in `EnrichmentService._enrich_listing()` and the engines.

The geocoding stage is explicitly isolated:

```python
try:
    geo = get_geocoding_adapter().geocode(listing.address)
    self.repo.save_enriched_field(...)
    self.repo.upsert_run(..., SUCCEEDED)
except Exception as e:
    self.repo.upsert_run(..., FAILED, str(e))
```

If geocoding fails, `lat` and `lng` remain `None`. Lease and fair-price work can still use listing and transaction evidence. Public transport and driving are recorded as unavailable because they need coordinates.

Journey failure is isolated per destination/mode. `_enrich_journeys()` catches a route-matrix exception, stores a `JourneyEstimate` with `TEMPORARILY_UNAVAILABLE`, marks that journey run failed, and continues to other modes/locations. It does not invalidate the general transport or driving rollup.

The public transport and driving engines also attach status to individual components. `ModelRollup` only sets `overall_score` when sufficient component weight is assessed. `PreferenceScoringEngine` therefore does not silently count a partial model as a complete numeric score.

The resulting behavior can look like:

```text
Price                  ✓ calculated
Space                  ✓ immediate
Public transport       ✗ not assessed / provider error
Driving                ✓ or ✗ independently
Schools                ✓ or unavailable
```

That is graceful degradation: return the useful sections and explain the missing section. It is not universal isolation. `_run_session_enrichment()` loops listings sequentially, but there is no broad per-listing `try/except` around `_enrich_listing()`. An unexpected exception in a fair-price or engine path can still abort the whole job. Known provider and data conditions are handled more carefully than arbitrary programming errors.

## 2.6 Detailed failure example: OneMap times out

```text
OneMap HTTP request times out
  ↓
httpx raises a timeout from LiveOneMapAdapter
  ↓
LiveOneMapAdapter.geocode() does not retry the same request
  ↓
EnrichmentService._enrich_listing() catches the exception
  ↓
GEOCODING run is marked FAILED with an error message
  ↓
lat/lng stay None
  ↓
lease and fair-price work may continue from listing/transaction data
  ↓
transport and driving stages become unavailable because coordinates are missing
  ↓
the comparison can still return immediate facts and any successful evidence
```

Relevant code is `apps/api/app/adapters/live/onemap.py -> LiveOneMapAdapter.geocode()`, `apps/api/app/services/enrichment_service.py -> _enrich_listing()`, and `apps/api/app/services/comparison_service.py -> get_comparison()`.

The user sees an unavailable or failed enrichment state, not a fake coordinate. In development, route/provider details can be more visible in logs or safe provider messages; production API responses use generic messages for sensitive provider failures.

## 2.7 Worker and database failure behavior

The worker boundary in `apps/api/app/worker_main.py -> run_enrichment_task()` works like this:

1. Load the durable job row.
2. Treat missing or already-terminal jobs as safely finished from the queue's perspective.
3. If a running job is stale, requeue it; if it is not stale, return `already_running`.
4. Atomically claim a queued job.
5. Run `EnrichmentService` with a progress callback.
6. Mark success, requeue a retryable failure, or mark a permanent failure.
7. Return a safe response.

`EnrichmentJobRepository.claim()` uses a conditional SQL update requiring `status == "queued"`. This is an idempotency guard: if the same task is delivered twice, only one delivery can make the row transition from queued to running.

Database persistence is intentionally short-lived around slow provider work. `EnrichmentRepository.batch()` commits related writes after calculations; a running marker is committed before slow external work. The code comments explicitly say the worker should not hold a database transaction open while waiting on Google or OneMap.

`apps/api/app/db/session.py` uses `pool_pre_ping=True`, bounded pool settings, a connection timeout and, in production, `sslmode=require`. These settings do not make a database outage disappear; they make stale connections and unbounded connection consumption less damaging.

## 2.8 Current limitations in failure handling

* OneMap live geocoding has no general retry/backoff policy.
* Google and OneMap provider calls do not share a single circuit breaker. A circuit breaker would stop sending calls to a repeatedly failing dependency, but I could not find a circuit-breaker implementation.
* A `FallbackRoutingProvider` exists in `apps/api/app/adapters/routing/fallback.py` and is unit-tested, but `get_routing_provider()` returns the live Google provider directly in live mode. The fallback wrapper is not the normal live factory path.
* An unexpected exception inside one listing's enrichment can still terminate the session job; per-listing continuation is not guaranteed for every programming error.
* The inline executor protects against duplicate sessions only inside one process with an in-memory set. That is not a distributed lock.

---

# 3. CACHING AND PERFORMANCE

Caching means storing the result of an expensive operation so it can be reused instead of recomputed or re-fetched.

Conceptually:

```text
First request:

"Blk 123 Ang Mo Kio Ave 6"
      ↓
OneMap API
      ↓
coordinates
      ↓
cache

Second request:

same address
      ↓
cache
      ↓
coordinates
```

Caching avoids work; it does not make the underlying provider more accurate.

## 3.1 Actual caches

| Cache | Stores | Cache key | TTL/lifetime | Why cached | Invalidation |
| --- | --- | --- | --- | --- | --- |
| Optional Redis route cache | Successful `RouteResult` objects for shared routing-provider methods | Versioned namespace plus SHA-256 of provider, method, rounded coordinates, mode, time bucket and extras | Walking 24h; transit 6h; traffic-aware driving 15m; non-traffic driving 24h | Avoid repeated Google Routes calls and latency | TTL expiry or namespace change; no manual invalidation API found |
| HDB availability process cache | List of official availability records | One process-wide value, not a per-key cache | `HDB_CARPARK_AVAILABILITY_CACHE_SECONDS`, default 60s | Avoid repeatedly downloading the same current feed during one process interval | TTL expiry or `reset_cache()` |
| Lease evidence cache | Exact-address lease evidence inside one `EnrichmentService` instance | Canonical block/street string `block|street` | Process/service-object lifetime; no TTL | Reuse transaction-derived lease evidence within an enrichment run | New service instance or explicit cache reset |
| Reference-data stores | LTA bus stops, MRT stations, MOE schools and carparks | Class-level singleton fields | Process lifetime | Avoid rereading static fixtures | Process restart or `reset_cache()` |
| CatBoost artifact cache | Loaded compatible model object | Resolved artifact directory path | Process lifetime | Avoid loading the model file for every prediction | Process restart or test-only clear |
| Next.js OneMap token cache | OneMap auth token for address suggestions | Single process-level token | 5 minutes | Avoid authenticating for every search | Expiry, 401 or 403 |
| TanStack Query browser cache | API query state and results | Query keys in the React app | Library/query configuration lifetime | Avoid duplicate browser requests and manage invalidation | Mutation invalidation/refetch; page/app lifecycle |

There is no general application-wide address-result cache in `LiveOneMapAdapter`. The `data-sources.md` table describing a planned 30-day OneMap cache is documentation, not an implemented API cache.

There is also an important split in routing. `GoogleRoutingProvider` in `apps/api/app/adapters/routing/google.py` uses the Redis-backed `RouteCache` for walking, driving routes, driving alternatives and transit routes. Its `get_route_matrix()` method does not use that cache. The older `LiveGoogleRoutesAdapter.route_matrix()` used for important-location journey matrices also has no cache. Therefore, do not claim that every Google route matrix is cached.

## 3.2 Cache keys and time buckets

`apps/api/app/adapters/routing/cache.py -> build_cache_key()` rounds coordinates to four decimal places, approximately an eleven-metre grid at the equator. This deduplicates tiny coordinate differences without treating distant stops as identical.

The key includes:

* provider name;
* method, such as `walking`, `driving_alts` or `transit`;
* rounded origin and destination;
* travel mode;
* a time bucket (`AM_PEAK`, `PM_PEAK`, `OFF_PEAK`, or `none`);
* extra routing preferences, such as traffic awareness;
* a configurable namespace, default `nearhome:routes:v1`.

The namespace matters because a cache entry made under one response or scoring contract should not necessarily be reused after a semantic change. Changing the namespace is a way to isolate old entries without knowing every key.

If the key accidentally omitted travel mode, a walking result could be returned for a driving request. If it omitted the time bucket, a traffic-aware morning route could be reused at night. If it omitted a routing preference, a traffic-aware and traffic-unaware answer could collide.

## 3.3 TTL and stale data

TTL means “time to live.” A 24-hour TTL means:

```text
Stored: Monday 10:00
Expires: Tuesday 10:00
```

NearHome uses shorter TTL for traffic-aware driving because traffic changes faster, six hours for transit structure, and one day for walking. HDB live availability defaults to 60 seconds and marks records older than 15 minutes as `STALE`.

Coordinates change relatively rarely; traffic changes quickly; resale transactions change when the snapshot is refreshed. These facts explain why different data types deserve different freshness policies. The repository does not implement a universal freshness policy for every source.

Only successful route results are written to `RouteCache`. A provider failure is not cached as if it were a successful answer. If Redis is absent or unreachable, `RouteCache` logs a safe warning and behaves as a no-op. The persisted PostgreSQL evidence remains the source of truth for completed enrichment.

## 3.4 Performance decisions

### Avoiding the work entirely

Caching is different from speeding up an operation. A faster Google request still costs a Google request. A cache hit avoids the request entirely.

### Reusing HTTP connection pools

`GoogleRoutingProvider` lazily creates one `httpx.Client` and reuses it for a worker job. This reuses connections instead of constructing a new client and TLS pool for every route call. `EnrichmentService` closes the provider at the end of the run.

### Bounded route concurrency

`apps/api/app/adapters/routing/batch.py -> run_bounded_route_calls()` uses a small `ThreadPoolExecutor`, deduplicates calls by key, retains input order, and attaches an exception to the individual outcome. The default limit is `ROUTE_REQUEST_CONCURRENCY=4`.

This lets independent route candidates overlap while preventing unlimited parallel provider calls. A failed candidate does not cancel valid candidates.

The whole listing loop is deliberately sequential in `EnrichmentService._run_session_enrichment()`. This limits simultaneous load and makes per-listing persistence easier to reason about. It means five listings do not all execute every provider call at once.

### Batch and short database transactions

`EnrichmentRepository.batch()` coalesces related writes. It commits the running status before a slow provider call, then commits the calculation results after the call. `CarparkRepository` imports refreshed static carpark data in one operation rather than doing a per-carpark mirror during every user enrichment.

### Static and precomputed data

The LTA/MRT/MOE fixture stores and the SLA-to-OSM road mapping are loaded from repository-managed artifacts. Major-road access does not scan or download the OSM graph at request time. The CatBoost model is trained as a build/maintenance step and loaded at runtime; inference does not fit a model.

## 3.5 A real request timeline

For independent route candidates, NearHome can execute up to four calls concurrently through `run_bounded_route_calls()`:

```text
Candidate A: 500 ms ─────────
Candidate B: 800 ms ─────────────────
Candidate C: 600 ms ───────────
Candidate D: 900 ms ─────────────────────

Bounded concurrent duration ≈ max(500, 800, 600, 900) + overhead
```

That statement applies to code paths that call the batch helper. It does not mean the entire enrichment job is concurrent: the listing loop and important-location matrix loop are still sequential in `EnrichmentService`.

## 3.6 Performance observability

`apps/api/app/services/enrichment_metrics.py` collects request-scoped metrics for enrichment:

* stage durations;
* route request/success/failure/timeout counts;
* cache hits and misses;
* database read/write counts and durations;
* commit count;
* total job duration.

`measure_stage()` emits `enrichment_stage_timing`; `collect_enrichment_metrics()` emits one `enrichment_performance_summary`. These are structured logs, not a Prometheus or OpenTelemetry metrics backend.

---

# 4. BACKGROUND JOBS AND WORKERS

## 4.1 Actual architecture

NearHome has multiple execution modes.

```text
Browser
   ↓
FastAPI POST /api/v1/sessions/{id}/enrichment/start
   ↓
Create or reuse PostgreSQL enrichment_jobs row
   ↓
Dispatch according to JOB_EXECUTION_MODE
   ├── inline: run in API process via a worker thread
   ├── arq: enqueue job ID in Redis; local ARQ worker consumes it
   └── cloud_tasks: enqueue job ID in Google Cloud Tasks
                         ↓
                  private Cloud Run worker
                         ↓
                  EnrichmentService
                         ↓
                  external APIs + deterministic engines
                         ↓
                  PostgreSQL enriched fields and run statuses
                         ↓
                  Browser polls status and refreshes comparison
```

Relevant files:

* `apps/api/app/jobs/queue.py`: inline and ARQ executors.
* `apps/api/app/jobs/tasks.py`: ARQ task definition.
* `apps/api/app/jobs/cloud_tasks.py`: Cloud Tasks task creation.
* `apps/api/app/worker_main.py`: private worker HTTP endpoint and state handling.
* `apps/api/app/services/enrichment_service.py`: shared enrichment workflow.
* `apps/api/app/repositories/enrichment_job_repository.py`: durable job state and atomic claim.
* `apps/api/app/api/routes.py`: start/status routes.

Production documentation configures `JOB_EXECUTION_MODE=cloud_tasks`. Docker Compose configures an ARQ queue with Redis. Inline mode exists for local/test compatibility and is rejected by production configuration validation.

## 4.2 Why use a worker?

Synchronous design:

```text
POST /compare
      ↓
HTTP request stays open while OneMap, Google, calculations and writes run
      ↓
response after perhaps many seconds
```

Queued design:

```text
POST /enrichment/start
      ↓
create durable job row
      ↓
return job ID / 202 Accepted

worker processes job independently
      ↓
frontend polls durable status
```

The queued design keeps the API request short, allows queue delivery to retry, lets the worker control expensive provider work, and lets processing continue after a browser disconnects. NearHome's code specifically returns HTTP 202 for the enrichment start route and stores coarse progress stages.

This does not guarantee every job finishes. It moves the long-running work to a boundary where status and retry behavior can be managed.

## 4.3 Queue and task payload

A queue is a waiting line of work. In NearHome, the payload is intentionally small:

```json
{"job_id": "..."}
```

`CloudTasksDispatcher.enqueue_enrichment()` creates a deterministic task ID `enrichment-{job_id}` and sends only the job ID to the private worker. The worker reloads current session/listing data from PostgreSQL. This avoids putting raw listing text or provider data into a queue message.

Cloud Tasks uses an OIDC token and the private worker is protected by Cloud Run IAM. `require_cloud_tasks_header()` adds a second production check for the Cloud Tasks header. The header is defense in depth; IAM is the actual public-access gate according to the code comments.

## 4.4 Job states

The durable `enrichment_jobs` table uses lower-case job statuses:

```text
queued → running → completed
   │        │
   │        ├── failed
   │        └── queued again when retryable/stale
   └──────────── failed if dispatch cannot start
```

The repository constants are:

* active: `queued`, `running`;
* terminal: `completed`, `failed`, `cancelled`.

The per-listing `enrichment_runs` table uses uppercase `PENDING`, `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED` and `UNAVAILABLE`, as defined by `EnrichmentStatus`.

Progress stages include values such as `queued`, `starting`, `preparing_data`, `geocoding_and_property`, `calculating_fair_price`, `calculating_transport`, `calculating_driving`, `checking_schools`, `calculating_journeys`, `finalising_results`, `retrying`, `completed` and `failed`.

The frontend maps stages to user-facing progress messages in `apps/web/src/lib/enrichment-progress.ts`. It derives progress from durable backend run statuses rather than pretending a running request is complete.

## 4.5 Idempotency

Idempotency means repeating the same operation produces the same safe final effect instead of duplicating work or data.

NearHome has several protections:

* `EnrichmentJobRepository.create_or_get_active()` returns the existing active job for the same session. The partial unique database index on active `(session_id, job_type)` handles a race between API instances.
* `claim()` uses `UPDATE ... WHERE status = 'queued'`. Only one duplicate task can claim the job.
* The worker returns safely when it sees a completed/failed/cancelled job or a non-stale running job.
* Cloud Tasks uses a deterministic task name and treats an already-existing task as the same job.
* Enriched fields and journey estimates are upserted by listing/field or listing/location/mode identity.

This is stronger than an in-memory “already running” flag. It is not a claim that every downstream provider call is globally exactly-once. If a process dies after an external call but before persistence, the provider may have seen the call and a later retry may call it again. The repository's goal is safe durable state transitions and idempotent database effects, not exactly-once external billing.

## 4.6 Worker failure walkthrough

If the worker crashes before claiming a job, the queue can deliver it again and it remains queued. If it crashes after claiming, the job can be detected as stale after the configured lease period and requeued or failed according to the caller path.

If `EnrichmentService` raises a `ValueError`, `worker_main.py` treats it as permanent, stores a safe failed message and returns a failed result. If it raises a timeout, transport error or selected retryable `AdapterError`, the repository moves the job back to queued and the worker returns 503 while attempts remain. Cloud Tasks can then redeliver it.

If the task is delivered twice at the same time, the first worker wins the conditional claim. The second sees a running job and returns `already_running` unless the running job is stale.

There is no separate dead-letter queue implementation in the repository. After the maximum configured attempts, the job becomes failed in PostgreSQL and the user is told to try again.

---

# 5. EXTERNAL API DESIGN, QUOTAS AND FALLBACKS

## 5.1 External and reference sources

| Provider/source | Used for | Access type | Called from |
| --- | --- | --- | --- |
| OneMap | Listing geocoding and address search | REST API | `LiveOneMapAdapter`; Next.js `/api/geocode` route |
| Google Places | Important-location and named-school suggestion/details | REST API | `LiveGooglePlacesAdapter`, selected through factory |
| Google Routes | Walking, driving, transit and route matrices | REST API | `GoogleRoutingProvider` and legacy `LiveGoogleRoutesAdapter` |
| Groq | Smart Paste structured extraction | LLM API | `LiveGroqAdapter` |
| data.gov.sg HDB resale snapshot | Fair-price transaction evidence | Downloaded/fixture-backed data | `FixtureHDBTransactionsAdapter` |
| data.gov.sg HDB Carpark Information | Static carpark locations and attributes | Maintenance download to fixture; optional DB mirror | `HdbCarparkStore`, `CarparkRepository` |
| data.gov.sg HDB Carpark Availability | Current/stale lot availability | REST API | `CarparkAvailabilityProvider` |
| LTA DataMall | Bus stops, routes and service frequency snapshots | Maintenance API/download | `data_pipeline/ingest_lta_reference.py`; runtime reads fixtures |
| MOE/data.gov.sg school snapshot | School locations | Snapshot/fixture | `ReferenceDataStore.schools()` and `SchoolsEngine` |
| SLA National Map Line | Official Major Road geometry | Offline GeoJSON artifact | `SlaMajorRoadStore` and road validation |
| OpenStreetMap/OSMnx | Local directed driving graph | Offline GraphML artifact | Major-road mapping/build scripts |
| Derived SLA→OSM mapping | Versioned legal road-entry candidates | Offline JSON artifact | `SlaOsmMajorRoadMappingStore` |

The runtime factory in `apps/api/app/adapters/factory.py` is the authoritative provider-selection point. HDB transactions, LTA, MRT, MOE and road artifacts are not live per-user calls during ordinary enrichment.

## 5.2 Why adapters exist

```text
NearHome engine/service
      ↓
provider-independent interface
      ↓
Google / OneMap / mock / fixture
```

`RoutingProvider` in `apps/api/app/adapters/routing/base.py` is a concrete example. Engines receive `RouteResult` and `RouteMatrixResponse`, not Google's JSON keys. The Google adapter owns URL construction, headers, field masks, duration parsing, polyline parsing and provider error metadata. The mock provider implements the same conceptual interface.

The external side of this boundary is validated by provider-specific Pydantic models in `apps/api/app/adapters/provider_schemas/`; the internal side is expressed as provider-independent dataclasses. This makes business logic easier to test, makes demo mode deterministic, prevents malformed provider structure from reaching engines, and prevents Google/HDB/LTA field names from leaking through the application. A future provider would need a schema plus adapter mapping rather than a rewrite of the scoring code.

## 5.3 Request design and validation by provider

### OneMap

`LiveOneMapAdapter` first obtains an access token, then calls the elastic search endpoint with `searchVal`, `returnGeom=Y`, `getAddrDetails=Y` and `pageNum=1`. It validates the token and search envelopes with `OneMapTokenResponse` and `OneMapSearchResponse`, then applies coordinate/address semantic checks and maps the validated fields into `GeocodeResult`.

The Next.js address-search route deliberately keeps credentials server-side, returns a small normalised suggestion, sends `Cache-Control: no-store`, and never exposes the OneMap token to the browser.

### Google Places

`LiveGooglePlacesAdapter` calls Places autocomplete with `includedRegionCodes: ["sg"]`. Details requests use a field mask of `id,formattedAddress,location`. `GooglePlacesAutocompleteResponse` validates the nested `suggestions[].placePrediction` fields, while `GooglePlaceDetailsResponse` validates identity, formatted address and coordinate structure. The adapter then returns canonical `PlaceSuggestion` and `PlaceDetails` objects.

### Google Routes

The newer `GoogleRoutingProvider` calls:

* `directions/v2:computeRoutes` for walking, driving, alternatives and transit;
* `distanceMatrix/v2:computeRouteMatrix` for route matrices.

It uses field masks to request only the fields the engine needs. Traffic-aware driving includes the relevant routing preference and departure time; non-traffic-aware driving omits `departureTime` because Google rejects that invalid combination.

Both endpoints validate their raw success payloads before mapping. Provider additions are ignored, but malformed fields NearHome consumes produce `invalid_output`; an explicitly empty route list remains the distinct no-route condition.

### Groq

`LiveGroqAdapter` sends bounded listing content, a strict JSON schema generated from `SmartPasteExtractionSchema`, low temperature and a capped completion budget. It validates the returned JSON again with Pydantic before converting it into candidate fields.

### Data files and artifacts

Data ingestion scripts perform the live download as a maintenance operation. HDB and LTA scripts validate the raw provider envelope/rows before transformation, then validate the normalized rows immediately before writing repository-managed fixtures or database mirrors. Runtime loaders revalidate HDB, LTA, MRT, MOE and rail rows before creating dataclasses or indexes. Cross-row and cross-file quality checks remain separate. For the SLA road data, road mapping and CatBoost model, Pydantic validates artifacts both before publication and when loading, before version/hash/feature compatibility checks; runtime does not silently rebuild an invalid artifact.

### Rail graph fixture build pipeline

`data_pipeline/build_rail_graph.py` is a maintenance-time compiler for the
curated MRT/LRT topology. It does not construct a NetworkX object and it is not
called during an ordinary enrichment request. It converts the hand-maintained
station topology into validated fixture files, after obtaining physical-station
coordinates from OneMap:

```text
STATIONS (physical names and codes)
        + LINE_SEQUENCES (ride order)
        + OneMap coordinates
                    |
                    v
          build_rail_graph.py
             /      |      \
            v       v       v
rail_lines_  rail_stations  rail_edges
structure.csv    .json         .csv
                    \           /
                     v         v
                    RailDataStore
                          |
                          v
                 RailGraph adjacency map
```

The important modelling decision is that a graph node is a **station-line
code**, not a physical station. For example, Jurong East is one physical
station but contributes two nodes:

```text
NS2 Bukit Batok
      |
      | NSL ride, 2.5 min
      v
NS1 Jurong East
      |
      | interchange transfer, 5.0 min
      v
EW24 Jurong East
      |
      | EWL ride, 2.5 min
      v
EW23 Clementi
```

This representation makes changing lines an explicit weighted edge. The
current curated source contains 184 physical stations, 216 station-code nodes,
29 physical interchanges and 247 stored undirected edges: 212 ride edges and
35 transfer edges.

#### Source structure: `STATIONS`, tap-out pairs and line names

`STATIONS` maps one physical station name to all station-line codes at that
location:

```python
STATIONS = {
    "Bukit Batok": ["NS2"],
    "Jurong East": ["NS1", "EW24"],
    "Dhoby Ghaut": ["NS24", "NE6", "CC1"],
}
```

`Bukit Batok` is a single-line station. `Jurong East` is a two-code
interchange. `Dhoby Ghaut` has three code nodes, so the edge builder later
creates all three transfer pairs: `NS24`-`NE6`, `NS24`-`CC1` and
`NE6`-`CC1`.

`TAP_OUT_PAIRS` stores the two interchanges whose paid-area transfer requires
exiting and re-entering:

```python
frozenset({"NS21", "DT11"})  # Newton
frozenset({"BP6", "DT1"})    # Bukit Panjang
```

The unordered `frozenset` means `NS21`-`DT11` and `DT11`-`NS21` identify the
same pair. These edges receive the six-minute tap-out weight instead of the
ordinary five-minute transfer weight.

`_line_for_code()` extracts the leading capital letters and maps the code
prefix to NearHome's canonical line label:

```python
_line_for_code("NS21")  # "NSL"
_line_for_code("CG1")   # "EWL"
_line_for_code("BP6")   # "BPLRT"
_line_for_code("STC")   # "SKLRT"
```

The explicit mapping matters because a code prefix is not always the desired
line label: the `CG` Changi branch is part of `EWL`, while the `STC` and `PTC`
centre nodes belong to their respective LRT systems rather than `NEL`.

#### Ride ordering: `LINE_SEQUENCES`

`STATIONS` says which nodes exist but not which ones are neighbours.
`LINE_SEQUENCES` supplies the actual ride order. A simplified sequence such as:

```python
sequence = ["NS1", "NS2", "NS3"]
```

is paired with its one-item-shifted form:

```python
list(zip(sequence, sequence[1:]))
# [("NS1", "NS2"), ("NS2", "NS3")]
```

Those pairs become two ride edges. Missing or reserved code numbers are
excluded explicitly, so the NSL sequence connects `NS5` directly to `NS7`
without inventing `NS6`. Loops repeat their origin at the end; for example,
the BPLRT loop ends `... BP12, BP13, BP6`, which creates its closing
`BP13`-`BP6` edge.

Branches are also explicit. The Changi Airport branch is:

```python
EWL_CHANGI_BRANCH = ["EW4", "CG1", "CG2"]
```

and therefore produces:

```text
EW3 -- EW4 -- EW5       main EWL
        |
       CG1 -- CG2       Expo, then Changi Airport
```

This is why the topology is not inferred merely by incrementing station-code
numbers: doing that would lose branch and loop behaviour.

#### `build_structure_rows()`

`build_structure_rows()` flattens the physical-station mapping into one row per
station-line code. Given:

```python
"Jurong East": ["NS1", "EW24"]
```

it returns these two row dictionaries:

```python
{
    "station_code": "NS1",
    "station_name": "Jurong East",
    "line": "NSL",
    "interchange_group": "JURONG_EAST",
}
{
    "station_code": "EW24",
    "station_name": "Jurong East",
    "line": "EWL",
    "interchange_group": "JURONG_EAST",
}
```

Both rows share the same `interchange_group`, showing that they are two code
nodes at one physical location. The resulting
`rail_lines_structure.csv` is a reference/inspection artifact; the runtime
graph loader uses the station JSON and edge CSV rather than this file.

#### `build_edges()`

For each line group, `build_edges()` looks up the group's ride weight and
turns every consecutive sequence pair into a row such as:

```python
{
    "from_node": "NS1",
    "to_node": "NS2",
    "line": "NSL",
    "edge_type": "ride",
    "estimated_minutes": 2.5,
}
```

Ride weights are structural approximations: most MRT groups use 2.5 minutes,
CCL uses 2.0 minutes and LRT uses 1.5 minutes. `seen_ride` stores each pair as
an unordered `frozenset`, preventing the same ride connection from being
emitted twice if sequences overlap.

The function then creates every unique pair of codes at each physical
interchange. Jurong East produces:

```python
{
    "from_node": "NS1",
    "to_node": "EW24",
    "line": "INTERCHANGE",
    "edge_type": "transfer",
    "estimated_minutes": 5.0,
}
```

Newton is found in `TAP_OUT_PAIRS`, so its corresponding output is:

```python
{
    "from_node": "NS21",
    "to_node": "DT11",
    "line": "TAP_OUT_TRANSFER",
    "edge_type": "transfer",
    "estimated_minutes": 6.0,
}
```

#### `geocode_stations()`

The topology is curated, but coordinates are not hand-entered.
`geocode_stations()` calls `LiveOneMapAdapter` once per physical station. It
first derives all lines at the station and chooses a query suffix:

```python
# South View has only BP2/BPLRT
query = "South View LRT STATION"

# Jurong East has NSL and EWL codes
query = "Jurong East MRT STATION"
```

A successful result is reduced to the canonical coordinate fields:

```python
coords["Jurong East"] = {
    "latitude": 1.3330275154394,
    "longitude": 103.742370487607,
}
```

If a call fails, that physical station receives `None` coordinates and the
failure is collected for reporting. The script deliberately continues so it
can write inspectable artifacts, but `main()` ultimately returns exit status
`1` when any coordinate is missing. A 0.05-second pause between calls reduces
request pressure on OneMap.

#### `main()` and publication validation

`main()` performs the build in this order:

```text
1. Create data_pipeline/fixtures/rail/
2. build_structure_rows() -> rail_lines_structure.csv
3. geocode_stations() -> coordinate dictionary
4. combine names, codes, lines, flags and coordinates
5. validate RailStationSnapshot -> rail_stations.json
6. build_edges()
7. validate list[RailEdgeSnapshotRow] -> rail_edges.csv
8. return 1 if coordinates are missing, otherwise return 0
```

For example, the published Jurong East station record is:

```json
{
  "station_name": "Jurong East",
  "codes": ["NS1", "EW24"],
  "lines": ["EWL", "NSL"],
  "is_interchange": true,
  "latitude": 1.3330275154394,
  "longitude": 103.742370487607,
  "active": true
}
```

The Pydantic publication schemas require non-empty station/line identifiers,
valid finite coordinates, a positive edge weight and an `edge_type` of exactly
`ride` or `transfer`. Running the file directly executes `main()` and exposes
its return value as the shell exit status:

```bash
python data_pipeline/build_rail_graph.py
```

#### Runtime loading and final adjacency graph

`RailDataStore.load()` revalidates `rail_stations.json` and `rail_edges.csv`,
converts the rows into `RailStation` and `RailEdge` dataclasses, and caches the
result for the process lifetime. `RailGraph.__init__()` then makes every stored
edge bidirectional:

```python
adjacency[from_node].append((to_node, minutes, edge_type, line))
adjacency[to_node].append((from_node, minutes, edge_type, line))
```

Thus the CSV stores `NS1,NS2,NSL,ride,2.5` once, while the in-memory graph
allows travel from `NS1` to `NS2` and from `NS2` to `NS1`.

A real shortest-path example from Bukit Batok to Clementi is:

```text
NS2 --NSL ride 2.5--> NS1
NS1 --transfer 5.0--> EW24
EW24 --EWL ride 2.5--> EW23
```

The hand-written Dijkstra search returns:

```python
RailPathResult(
    total_minutes=10.0,
    node_path=["NS2", "NS1", "EW24", "EW23"],
    line_path=["NSL", "INTERCHANGE", "EWL"],
    transfers=1,
)
```

These minutes support structural reachability and transfer-count analysis;
they are not a live timetable prediction. User-facing journey duration must
still come from the configured `RoutingProvider`.

## 5.4 Quotas and rate limits

A **rate limit** is a maximum request rate, such as 100 requests per minute. A **quota** is a provider allowance over a period or billing dimension. If NearHome sends too many calls, a provider may return HTTP 429 or a quota error.

The repository does not encode exact current provider quotas or prices for Google, OneMap, LTA, Groq or data.gov.sg. Those must be checked in each provider's current documentation/account. It would be wrong to quote a number merely because a generic guide used one.

NearHome does have protections:

* Application-level `RateLimitMiddleware` applies a configured limit, default 60 requests per minute, to API paths listed in `apps/api/app/core/rate_limit.py`.
* Route calls use bounded concurrency, default four.
* Route cache keys and TTLs can reduce repeated Google calls when Redis is configured.
* The worker limits enrichment concurrency with `MAX_CONCURRENT_ENRICHMENTS`, default one, in the inline/queue compatibility layer.
* Docker/production worker configuration uses conservative Cloud Run concurrency and one maximum worker instance in the documented deployment setup.
* Smart Paste caps input length and LLM content length.
* The comparison flow limits the shortlist to the product's intended 2–5 listings in the UI/product behavior, although the manual Pydantic schema itself does not show a five-listing maximum. The exact enforcement should therefore be checked in the page/service before claiming the API alone enforces that limit.

## 5.5 What happens on HTTP 429?

Google routing converts 429 into `RoutingProviderError` with `retryable=True`, `http_status=429`, a parsed provider error code and request ID. It logs structured diagnostics without logging the API key. The worker can retry only if the exception crosses the worker boundary in a recognised retryable form. In the normal engine path, route failures are often attached to individual component outcomes, so a 429 can instead produce a provider-error/unavailable component and allow the comparison to continue.

The application does not have a provider-specific “wait until Retry-After” implementation or a general exponential backoff loop. The code classifies the failure and may degrade/requeue depending on where it occurs.

## 5.6 Fallback map

| Primary | Fallback or alternative | Current status |
| --- | --- | --- |
| OneMap original search query | Prefix-stripped and `, Singapore` query variants to OneMap | Implemented query variants, not a different source |
| Live OneMap adapter | Mock OneMap when `DEMO_MODE` is true or the OneMap email is absent in the factory | Implemented, configuration-dependent |
| Live Google Places | Mock Places when demo mode or API key is absent | Implemented, configuration-dependent |
| Live Google Routes | Mock routing when demo mode or API key is absent | Implemented only as an explicit factory selection; live provider errors are not replaced by mock data |
| CatBoost fair-price artifact | Weighted-comparable estimate | Implemented in `FairPriceEngine`; runtime never trains as fallback |
| Specific comparable filters | Explicitly labelled relaxed filter stages and broad fallback | Implemented; confidence/warnings record the relaxation |
| HDB flat model evidence | Conservative transaction inference | Implemented only after explicit subtype/listing evidence is unavailable |
| HDB availability live feed | Static carpark data/history or an unavailable/not-covered state | Implemented in parking engine; missing availability is not zero |
| Google routing provider | `FallbackRoutingProvider` wrapper | Class exists and is tested, but normal live factory does not wrap the provider |
| HTTP listing retrieval | Bounded Playwright browser retrieval | Implemented only for eligible blocked/JS-shell cases; no CAPTCHA bypass |
| Smart Paste page retrieval | User copy-and-paste fallback | Implemented when the page remains unusable |

The most important distinction is between mock selection and live fallback. In `get_routing_provider()`, live mode with a Google key returns `GoogleRoutingProvider`; a live Google failure is intentionally allowed to become a provider error rather than fake demo routes. This protects the meaning of the result.

## 5.7 Provider failure and user impact

```text
Provider error
  ↓
adapter parses/classifies it
  ↓
engine or service chooses a local failure boundary
  ↓
component/run becomes provider_error, unavailable or failed
  ↓
other deterministic/immediate sections continue when possible
  ↓
UI labels the missing evidence instead of showing a fabricated score
```

This is preferable to silently switching from live Google data to fake demo data while still labelling the result as live.

---

# 6. TESTING SUITES

Testing asks:

> Does the code behave as intended under controlled examples?

The NearHome repository contains backend pytest tests, frontend Vitest/React Testing Library tests, and Playwright browser tests. It also contains evaluation/benchmark code for fair-price models.

## 6.1 Actual test map

```text
apps/api/app/tests/
├── adapter, schema and API tests
├── deterministic engine tests
├── routing/cache/batch tests
├── Smart Paste and retrieval tests
├── worker/Cloud Tasks/job tests
├── artifact/configuration tests
└── data-quality and transport-reference tests

apps/web/src/**/*.test.ts(x)
└── Vitest and React Testing Library tests for API helpers,
    progress, scoring display helpers and UI components

tests/e2e/
├── specs/comparison.spec.ts
├── specs/fair-price.spec.ts
└── Playwright configuration with local API and web servers
```

At the time of the provider-validation update there are 27 backend `test_*.py` files; counts can change as the repository evolves, so the file list and protected behaviors are more important than memorising a number.

## 6.2 Test areas

| Test area | Test files | What it protects |
| --- | --- | --- |
| Schemas and inputs | `test_schemas.py`, `test_api_integration.py` | Pydantic bounds, priority limits, named-school normalisation and API contracts |
| External provider schemas | `test_provider_schemas.py`; `apps/web/src/app/api/geocode/route.test.ts` | Nested OneMap/Google/HDB structures, raw maintenance envelopes, normalized snapshot rows, top-level matrix arrays, versioned artifact metadata and the TypeScript OneMap boundary |
| HDB addresses | `test_hdb_address_accuracy.py`, `test_geocoding_accuracy.py` | Canonical block/street matching and Singapore coordinate safety |
| Smart Paste | `test_smart_paste.py`, `test_smart_paste_retrieval.py` | Extraction validation, subtype/lease rules, conflicts, blocked pages, redirects and browser fallback |
| Immediate/recommendation engines | `test_engines.py`, `test_comparison_service.py` | Scores, requirements, ties, missing data and recommendation trace behavior |
| Fair price | `test_fair_price_artifact.py`, `test_model_evaluation.py`, fair-price cases in `test_engines.py` | Artifact compatibility, no runtime retraining, comparable fallback, filters and model evaluation helpers |
| Public transport | `test_public_transport_engine.py`, `test_transport_data.py`, `test_transport_reference_invariants.py`, `test_rail_graph.py` | Bus/rail evidence, missing/incomplete snapshots, corridor logic and graph invariants |
| Driving/roads | `test_driving_engine.py`, `test_google_roads_validation.py`, `test_major_road_mapping.py`, routing tests | Component rollups, strict road-entry validation, polyline/map matching and artifact hashes |
| Parking | `test_hdb_carpark_data.py` | Static deduplication, stale/missing availability, no false zero and parking persistence behavior |
| Routing adapters | `test_routing_provider.py`, `test_routing_batch.py` | Google request shapes, malformed payloads, error metadata, cache keys, connection reuse and bounded calls |
| Jobs/workers | `test_job_execution.py`, `test_enrichment_worker.py`, `test_cloud_tasks.py` | Inline/ARQ dispatch, Cloud Tasks payload, atomic claim, duplicate delivery and retry classification |
| API/database | `test_api_integration.py` | Health/readiness, session/listing/profile/comparison flows and stale job polling |
| Production configuration | `test_production_config.py` | Fail-closed production settings and artifact requirements |
| Frontend API/progress | `apps/web/src/lib/api.test.ts`, `wait-for-enrichment.test.ts`, progress tests | Request timeouts, parsing, polling and progress messages |
| Frontend UI | `paste-help-dialog.test.tsx` and component/helper tests | Accessible UI behavior and deterministic presentation helpers |
| Browser workflows | `tests/e2e/specs/comparison.spec.ts`, `fair-price.spec.ts` | Realistic page interactions, profile/listing/comparison flows, removal, Smart Paste and responsive behavior |

## 6.3 Unit tests

A unit test tests one small unit in isolation. NearHome examples include:

* `test_routing_provider.py -> test_malformed_success_payload_cannot_become_a_zero_minute_route()` passes malformed Google route dictionaries directly to `_route_to_result()` and verifies that an exception is raised.
* `test_routing_provider.py -> TestCacheKey` checks that identical inputs produce a stable key, modes/time buckets differ, and tiny coordinate differences round to the same grid.
* `test_schemas.py` checks the Pydantic contract without starting a full browser.
* `test_provider_schemas.py` validates raw provider-shaped dictionaries and arrays, and verifies that malformed live adapter responses become safe typed provider errors.
* Vitest tests such as `apps/web/src/lib/api.test.ts` verify request timeout selection using a mocked `fetch`.

Unit tests are fast and precise, but they do not prove that all components are wired correctly together.

## 6.4 Integration tests

An integration test exercises multiple real NearHome components together. `apps/api/app/tests/test_api_integration.py` uses FastAPI `TestClient` against the application and a PostgreSQL-backed session. It covers creating a session, saving a profile, adding listings, getting a comparison, duplicate detection, Smart Paste flow and local enrichment job behavior.

The test module is skipped unless `DATABASE_URL` is set. `tests/e2e/playwright.config.ts` starts the API with the active environment, so the browser suite can also exercise real HTTP/database wiring when configured.

There is no checked-in `tests/integration/` directory despite the older `docs/testing-strategy.md` mentioning one as a Phase 2 plan. The actual API integration tests live under `apps/api/app/tests/`.

## 6.5 End-to-end tests

An end-to-end (E2E) test drives a realistic workflow through the user-facing entry point. The Playwright tests navigate the Next.js app, click buttons, fill forms, intercept selected API calls where determinism is needed, and verify the visible result.

Actual E2E coverage includes:

* profile-first navigation;
* multiple named schools;
* important-location rehydration;
* priority controls and keyboard reordering;
* shortlist removal and rollback after server failure;
* duplicate-listing warning;
* manual two-listing comparison;
* Smart Paste guide, discard and confirm behavior;
* enrichment start/polling behavior;
* fair-price presentation without raw diagnostics;
* responsive/mobile comparison;
* the How It Works page and anchor links.

The E2E suite is not the same as a production test against every real provider. Many tests intercept APIs or rely on demo/local fixture behavior, which is intentional for determinism.

## 6.6 Regression tests

A regression test is a test for a bug or failure mode that should not come back. NearHome has tests that look like regression protection even when the original issue is not documented in the test name:

* malformed Google duration cannot become zero minutes;
* malformed provider envelopes and nested rows cannot reach canonical dataclasses;
* provider-added fields do not break models when NearHome does not consume them;
* `departureTime` is omitted for traffic-unaware driving;
* a normal Cloudflare CSP does not incorrectly block a rendered Smart Paste page;
* a private-IP redirect is rejected;
* a live routing error is not silently replaced by a mock provider;
* a stale enrichment job does not remain pollable forever;
* deleting one listing does not delete the buyer profile or unrelated listings;
* a parking availability failure does not become score zero;
* partial component evidence does not silently count toward a complete recommendation score.

## 6.7 Fixtures, mocks, fakes and stubs

A fixture is stable test input. NearHome stores fixture datasets in `data_pipeline/fixtures/`, including HDB transactions, HDB carparks, bus data, rail data, schools and road artifacts. This allows tests and demo mode to run without paid providers or unpredictable live responses.

A mock is a replacement that returns controlled behavior. `apps/api/app/adapters/mock/` contains mock OneMap, Groq, Google Places, Google Routes and HDB transaction adapters. A fake is a lightweight test object implementing the part of an interface the test needs. For example, `test_cloud_tasks.py` defines `FakeTasksClient`; `test_routing_provider.py` uses fake HTTP clients.

Tests use these replacements because live APIs would make tests slower, expensive, flaky, rate-limited and impossible offline. The adapter boundary makes replacement possible.

## 6.8 What the suite protects and what it does not

Strongly protected areas include deterministic scoring, schema validation, provider parsing, Smart Paste safety, route cache-key behavior, road artifact validation, worker state transitions and many core browser workflows.

Gaps or limitations supported by repository inspection:

* The integration suite requires a configured database; it is not a fully self-contained test database setup.
* There is no visible load-test suite or automated measurement of high-concurrency behavior.
* There is no live-provider contract test that regularly checks current Google/OneMap/Groq schemas without spending API quota.
* The route cache is unit-tested, but there is not a full multi-instance Redis integration test in the repository.
* There is no explicit circuit-breaker, dead-letter-queue or chaos test suite.
* The `data-sources.md` statement that every adapter has timeouts, retries and caches is broader than the current code; the tests and code show these mechanisms are provider-specific.
* The documented `tests/integration/` plan is not the actual current directory layout.

These are not reasons to discard the current tests. They identify what the current suite gives confidence about and where production confidence would still need operational checks.

---

# 7. MONITORING AND OBSERVABILITY

Testing asks:

> Does this work before deployment?

Monitoring asks:

> Is this still working after deployment?

Observability usually includes logs, metrics and traces. NearHome has strong structured logging and request-scoped enrichment measurements, but it does not contain a full metrics/tracing platform integration.

## 7.1 Logging

`apps/api/app/core/logging.py` configures `structlog` with JSON rendering, timestamps and log levels. `RequestLoggingMiddleware`:

1. accepts an incoming `X-Request-ID` or generates a UUID;
2. binds request ID, method and path to the logging context;
3. records completion status and duration;
4. logs unhandled exceptions as `request_failed`;
5. returns the request ID in the response header and generic 500 body.

Provider and worker logs add fields such as provider, job ID, session ID, attempt, stage, status, error category, error type and duration. Google diagnostics include endpoint, field mask, whether a key is present, status and provider request ID, while avoiding the API key. Production suppresses raw provider response bodies in places where they could leak sensitive details.

Enrichment logs include:

* `enrichment_job_running`;
* `enrichment_job_completed`;
* `enrichment_job_execution_failed`;
* `enrichment_stage_timing`;
* `enrichment_performance_summary`;
* `route_cache_unavailable`, read and write failures;
* Google route diagnostic events;
* model artifact load/invalid/missing events.

The performance documentation says aggregate enrichment logs do not include API keys, provider headers, raw addresses or complete provider payloads.

## 7.2 Metrics

The repository explicitly emits measurements through `EnrichmentMetrics`, including route counts, route failures/timeouts, cache hits/misses, database read/write timings, commit count and stage timings. These are emitted as structured log events.

I could not find application code exporting Prometheus metrics, OpenTelemetry spans, a Sentry client, a Datadog client or a custom queue-depth metric. Cloud Run, Cloud Tasks, Vercel, PostgreSQL and managed providers may expose infrastructure metrics in their own consoles, but those are not the same as NearHome explicitly emitting them.

## 7.3 Tracing

The application has correlation/request IDs, which help link a browser/API event to logs. That is useful request correlation, not distributed tracing. I could not find OpenTelemetry trace instrumentation or a trace exporter in the current repository.

## 7.4 Health checks

`GET /api/v1/health` is a liveness-style endpoint. It returns status `ok`, demo mode, release SHA and `checks: {"api": "ok"}`. It proves that the FastAPI process can answer; it does not check PostgreSQL, Redis, Google, OneMap or the worker.

`GET /api/v1/ready` checks PostgreSQL with `SELECT 1`. It checks Redis only when `JOB_EXECUTION_MODE=arq`. In inline and Cloud Tasks mode, Redis is reported as `not_required`. It returns:

* 503/unavailable when PostgreSQL is unavailable;
* ready when the required database is available;
* degraded when ARQ is selected but Redis is unavailable.

The health and readiness responses include the configured release SHA, which helps confirm which revision answered a request.

The worker has its own startup validation and private task endpoint, but I could not find a separate public worker health endpoint.

## 7.5 Alerts

I could not find checked-in alert policies or an alerting configuration for comparison failure rate, Google 429 spikes, queue age, worker failure rate or database latency.

Recommended improvement—not currently implemented: configure alerts around the signals already available from logs/infrastructure, especially:

* readiness failures;
* percentage of enrichment jobs ending failed;
* queue age or continuously queued jobs;
* repeated provider 429/5xx events;
* route cache failure rate if Redis becomes part of production;
* Cloud Run error and instance saturation metrics.

This project does not need an enterprise observability platform before it has users. A small log-based dashboard and a few high-value alerts would be proportionate.

## 7.6 Investigating “my comparison is stuck”

The current investigation path is:

```text
1. Check browser network request and X-Request-ID
2. GET /api/v1/ready to check API/database readiness
3. Inspect enrichment job status and progress_stage
4. Check per-listing enrichment run statuses
5. Search structured logs by job_id/session_id/request_id
6. Check worker logs for claim, retry or permanent failure
7. Check provider diagnostics and route/cache metrics if the stage is external
8. Check PostgreSQL rows if status and UI disagree
```

If the job is `queued`, investigate dispatch/queue configuration. If it is `running` with an old `updated_at`, stale-job handling should eventually requeue/fail it. If it is `failed`, inspect the safe `error_code` and server-side internal detail. If the job is completed but the UI is stale, inspect frontend polling/refetch and the comparison endpoint.

The available signals are sufficient for many incidents, but there is no single built-in incident dashboard or trace viewer.

---

# 8. COST MANAGEMENT

Cost management starts by identifying what creates usage. Exact current provider prices are not encoded here and must be checked against the provider's latest pricing documentation.

## 8.1 Cost map

| Cost area | Why NearHome may incur cost | Code/config evidence |
| --- | --- | --- |
| Managed PostgreSQL/Supabase | Database storage, connections and queries | SQLAlchemy repositories, production deployment docs |
| Cloud Run API | API instance time and requests | `cloudbuild.yaml`, deployment scripts |
| Cloud Run worker | Enrichment execution, CPU/memory and possible cold starts | `apps/api/Dockerfile`, worker deployment docs |
| Cloud Tasks | Enrichment task delivery | `apps/api/app/jobs/cloud_tasks.py` |
| Google Routes | Route and matrix calls | `GoogleRoutingProvider`, `LiveGoogleRoutesAdapter` |
| Google Places | Autocomplete/details for important locations | `LiveGooglePlacesAdapter` |
| OneMap | Auth and geocoding calls | `LiveOneMapAdapter`, Next.js geocode route |
| Groq | Smart Paste LLM input/output tokens | `LiveGroqAdapter` |
| Redis, if provisioned | Managed Redis instance and operations | Optional `REDIS_URL`, route cache and ARQ local setup |
| Data ingestion/hosting | Maintenance downloads and artifact storage | `data_pipeline`, fixture/model artifacts |
| Vercel | Web hosting/build/runtime usage | Next.js deployment architecture docs |

NearHome's live external calls are server-side. That keeps credentials out of the browser but means backend usage needs to be budgeted and monitored.

## 8.2 Fixed-ish versus usage-based costs

Database and hosted service plans are relatively fixed until capacity or usage tiers change. Cloud Run execution, Cloud Tasks deliveries and provider calls scale with activity. Google/OneMap/Groq costs depend on request/token volume and current account pricing.

No exact dollar amount should be stated from this repository because no pricing table is checked in.

## 8.3 Where request volume comes from

For a session with multiple listings and important locations, request volume grows with the number of listing origins, destinations, modes and route candidates. A conceptual matrix is:

```text
number of listings
  × number of important locations
  × selected journey modes
```

The public transport and driving models also make route calls for evidence such as walking access and driving alternatives. The exact count varies with available candidates and model configuration. `EnrichmentMetrics.route_request_count` records the actual count per enrichment job, which is better than guessing from a simplified formula.

Smart Paste cost is driven by bounded input content and the LLM completion budget. `MAX_LLM_CONTENT_CHARS=15_000` and `GROQ_MAX_COMPLETION_TOKENS=900` cap the size of a request, though prompt and provider pricing rules still need external verification.

## 8.4 Cost-control mechanisms

* **Route cache:** A warm route result can avoid a Google call. Redis is optional; without it, correctness remains but cross-process cache sharing is absent.
* **Bounded concurrency:** Four route calls by default limits simultaneous provider usage and protects the worker from an uncontrolled burst.
* **Deduplication:** `run_bounded_route_calls()` removes duplicate call keys before submitting provider work.
* **Short field masks:** Google adapters request only needed fields, reducing response size and unnecessary parsing.
* **Route summaries:** Major-road candidate ranking can request duration/distance without polyline geometry; geometry is requested only when needed for validation/connectivity.
* **Static/precomputed datasets:** LTA, school, carpark, road and transaction snapshots prevent every user comparison from calling a live source for the same reference data.
* **Prebuilt model artifact:** CatBoost training occurs outside request handling; runtime loads the artifact and falls back to comparables if incompatible.
* **Worker concurrency limits:** The deployment uses conservative worker scaling/concurrency, which trades throughput for cost and provider protection.
* **Input limits:** Smart Paste has 100,000-character request validation and a 15,000-character provider-content cap; the browser geocode route limits queries to 200 characters.
* **Application rate limit:** `RATE_LIMIT_PER_MINUTE` defaults to 60 for configured API paths.
* **Short-lived availability cache:** HDB availability is cached for 60 seconds by default, avoiding a feed request for every nearby-carpark lookup in one process interval.

Conceptual example:

```text
Without a route cache:
100 repeated requests → roughly 100 provider calls

With a successful shared cache entry:
first request → provider + cache
next requests → cache until TTL expiry
```

That is a mechanism explanation, not a claim that the current repository has measured a 100-to-1 production reduction.

## 8.5 Accuracy versus cost trade-offs

NearHome makes several visible trade-offs:

* Traffic-aware driving is more context-sensitive and potentially more expensive than a simple geometric estimate, so it is cached for only 15 minutes and used selectively.
* Major-road access uses precomputed local road topology plus Google route geometry. This costs route calls but avoids pretending that nearest-road distance proves legal vehicle access.
* Static reference snapshots are cheaper and deterministic at request time, but they can become stale and need a maintenance refresh.
* CatBoost is more computationally involved than a median PPSM estimate, so it is trained offline and loaded from an artifact. Weighted comparables remain the transparent fallback.
* The app computes detailed enrichment after immediate comparison, so a user can see basic facts without paying for every live provider call before the first useful response.

## 8.6 Cost explosion risks and current protection

| Risk | Protection | Remaining concern |
| --- | --- | --- |
| Unlimited route fan-out | Candidate limits, bounded thread pool, route metrics | Exact production budget per session is not a single central policy |
| Repeated same routes | Optional Redis cache and batch deduplication | Redis is not provisioned/configured by this repository's Cloud Tasks setup |
| Duplicate enrichment jobs | Partial unique index and deterministic task IDs | External provider calls are not exactly-once |
| Repeated transient failures | Worker attempt cap and provider classification | No custom exponential backoff or circuit breaker |
| Large Smart Paste input | 100k API limit and 15k LLM content cap | LLM request cost still depends on prompt/provider pricing |
| Live calls during tests | Fixtures, mocks and test monkeypatches | Some integration/E2E environments need explicit setup |
| Runtime model training | Prebuilt artifact and no-inference-fit behavior | Artifact must be built/refreshed as transaction snapshot changes |
| Stale static data | Explicit ingestion scripts and source metadata | Refresh is an operational process, not automatic user-request behavior |

---

# 9. HOW THE RELIABILITY MECHANISMS WORK TOGETHER

For a normal enrichment flow, the system is best understood as a chain of boundaries:

```text
User confirms listing
    ↓
Pydantic request validation
    ↓
Canonical listing/address/provenance
    ↓
Durable enrichment job row
    ↓
Worker dispatch and atomic claim
    ↓
Cache lookup where a route-cache path supports it
    ↓
External provider call or static reference data
    ↓
Timeout/error/response validation
    ↓
Deterministic engine calculation
    ↓
Fallback or explicit unavailable state
    ↓
Short PostgreSQL persistence transaction
    ↓
Per-stage status and progress
    ↓
Structured logs and performance summary
    ↓
Frontend polling/refetch and explanation
```

The concerns overlap:

```text
                    ┌── Data quality
                    │
External APIs ──────┼── Error handling
                    │
                    ├── Caching
                    │
                    ├── Quota control
                    │
                    └── Monitoring
```

For example, caching is both a performance feature and a quota-control feature. Validation is both a data-quality feature and a failure-containment feature. Worker status is both a reliability feature and an observability signal.

NearHome's immediate comparison is deliberately separate from external enrichment. `ImmediateComparisonEngine` can compare facts already confirmed by the user without calling OneMap, Google or Groq. That is a Tier 1 product choice with a Tier 2 benefit: a provider outage does not remove all usefulness from the product.

---

# 10. ONE COMPLETE FAILURE WALKTHROUGH

## 10.1 Happy path: listing geocoding

```text
ConfirmedListing.address
  → EnrichmentService._enrich_listing()
  → get_geocoding_adapter()
  → LiveOneMapAdapter.geocode()
  → OneMap auth token and elastic search
  → _is_usable_match()
  → GeocodeResult
  → EnrichmentRepository.save_enriched_field("geocode", ...)
  → EnrichmentRun GEOCODING = SUCCEEDED
  → coordinates passed to transport/driving/schools
  → ComparisonService returns derived results
```

The result stores provider, provenance and retrieval data. OneMap's provider-specific shape is not passed directly into the engines.

## 10.2 Failure path: OneMap returns a wrong-looking result

```text
OneMap returns JSON with a real coordinate
  → coordinate is outside 1.15–1.49 / 103.58–104.12
  → _is_usable_match() returns False
  → adapter tries the next allowed query variant
  → if no acceptable result exists, geocode raises ValueError
  → GEOCODING run is FAILED
  → downstream coordinate-dependent stages are unavailable
  → comparison still returns immediate/listing/other valid evidence
```

The system does not substitute the bad coordinate with the nearest valid Singapore coordinate. That would produce a precise-looking but false result.

## 10.3 Failure path: OneMap timeout

```text
httpx timeout
  → no general OneMap retry loop
  → _enrich_listing() catches the exception
  → geocoding run FAILED
  → lease/fair price may continue
  → transport/driving unavailable for missing coordinates
  → structured request/stage logs identify the failure
```

This is graceful degradation at the listing-stage boundary, not an absolute guarantee that every later calculation can never raise.

## 10.4 Failure path: Google route 429

```text
Google returns 429
  → GoogleRoutingProvider parses status/error/request ID
  → RoutingProviderError(retryable=True, http_status=429)
  → route batch keeps the error attached to that candidate
  → engine can exclude the failed candidate and assess other evidence
  → component may become provider_error/not_assessed
  → no fake mock route is substituted in live mode
  → structured provider diagnostics are logged
```

If the same error reaches the worker as an uncaught retryable exception, the job can be requeued up to its attempt limit. In many normal engine calls it is deliberately contained earlier and becomes a partial/unavailable component. The exact result depends on which route API and engine path was used.

---

# 11. PRODUCTION FAILURE SCENARIOS

| Scenario | Detection | Current response | User impact | Recovery |
| --- | --- | --- | --- | --- |
| OneMap unavailable | `httpx` exception, non-2xx response or no usable match | Geocoding run failed; query variants may be tried; coordinate-dependent stages become unavailable | No transport/driving/school evidence for that listing; immediate comparison can still work | Retry enrichment manually; inspect OneMap credentials/service |
| Google 429 | `RoutingProviderError.http_status == 429` and provider logs | Classified retryable; route candidate/component can degrade; worker retries only if error reaches worker retry boundary | Some route-based evidence unavailable or delayed; no fake live result | Wait for quota window, reduce load, verify billing/quota |
| Invalid coordinates | OneMap bounds check; provider parsers; static fixture checks | Reject/skip the value; no downstream route | Location-dependent metric unavailable | Correct listing/source data and rerun |
| Redis unavailable | Cache ping/read/write exception; readiness check in ARQ mode | Route cache no-ops; ARQ readiness can be degraded; Cloud Tasks/inline do not require Redis | Possible extra provider latency/cost; queued ARQ work cannot run without Redis | Restore Redis or use Cloud Tasks/inline mode where appropriate |
| Worker crashes | Queue redelivery, stale job timestamp, worker logs | Job can be redelivered/requeued; stale handling bounds indefinite polling; attempt cap ends in failed | Enrichment delayed or fails; immediate facts remain | Inspect worker/job logs and restart/redeploy |
| Database unavailable | `/ready` `SELECT 1`, SQLAlchemy errors, request logs | Readiness returns 503; request/worker persistence can fail | Session/comparison/job state unavailable | Restore PostgreSQL/network/credentials |
| Model artifact missing | `load_artifact()` checks files/metadata | Returns `None`; fair price uses weighted comparables or insufficient evidence | Model estimate may be replaced by transparent fallback | Build/deploy matching artifact; rerun enrichment |
| LTA fixture unusable | `DataQualityReport` thresholds | Transport components become not assessed instead of using broken network data | Public-transport score unavailable | Refresh and validate fixture |
| Smart Paste page blocked | Retrieval classification and bounded Playwright fallback | Browser fallback may run; otherwise copy/paste fallback is returned | User must paste listing text manually | Use supported page or copy/paste |
| Cloud Tasks enqueue failure | Dispatcher exception | Job row marked enqueue failed with safe message | Enrichment does not start | Fix queue/IAM/configuration and start again |

The table intentionally distinguishes “unknown” from “handled.” For example, the repository has no universal guarantee that an arbitrary internal exception in a per-listing engine is isolated from the rest of the job.

---

# 12. CURRENT IMPLEMENTATION VS RECOMMENDED PRODUCTION IMPROVEMENTS

## 12.1 Currently implemented

Code-confirmed mechanisms include:

* Pydantic request schemas and enum validation.
* Smart Paste strict schema validation, bounded content and user confirmation.
* Canonical HDB address keys and duplicate constraints.
* Singapore coordinate bounds for live OneMap results.
* Provider response validation for routes, carparks, LTA data and model artifacts.
* Provenance, status, confidence, assumptions, source and retrieval timestamps.
* Immediate comparison without external provider dependency.
* Per-stage enrichment runs and durable job status.
* Inline, ARQ/Redis and Cloud Tasks/Cloud Run execution modes.
* Atomic job claim and active-job uniqueness protection.
* Selected bounded retries and retryable/permanent classification.
* Optional Redis route cache with versioned keys and TTLs.
* In-memory static/reference caches and a five-minute Next.js OneMap token cache.
* Bounded route concurrency and duplicate-call suppression.
* Precomputed static data and prebuilt CatBoost artifacts.
* Structured JSON logs, request IDs, health/readiness endpoints and enrichment performance logs.
* Backend unit tests, database integration tests when configured, frontend tests and Playwright workflows.

## 12.2 Recommended improvements

These are recommendations, not claims about current behavior.

### Add explicit provider retry/backoff policy

Current situation: retry behavior is provider-specific and there is no application exponential-backoff implementation.

Risk: a short burst of transient failures may either fail too quickly or cause repeated immediate calls.

Recommendation: add a small, provider-aware retry utility with bounded attempts, jittered exponential backoff and `Retry-After` support where safe. Never retry permanent validation/authentication errors.

Why it helps: it improves recovery from temporary network/5xx conditions without turning a quota problem into a larger quota problem.

### Add a circuit breaker only if failure volume justifies it

Current situation: no circuit breaker was found.

Risk: during a sustained provider outage, every job may keep attempting the same failing dependency.

Recommendation: a simple provider-level open/half-open/closed circuit around repeated failures.

Why it helps: it protects latency and quota, but it adds state and tuning. For a student project, it is less urgent than good retry classification and alerts.

### Isolate unexpected errors per listing/stage

Current situation: known geocoding/journey/parking failures are isolated, but `_run_session_enrichment()` does not wrap every listing in an independent exception boundary.

Risk: one unexpected calculation bug can terminate a multi-listing job.

Recommendation: add a carefully designed per-listing terminal status and continue to other listings, while still surfacing a job-level failure when the result is unsafe.

Why it helps: it matches the product's comparison model, but must avoid hiding programming bugs.

### Add provider and job dashboards/alerts

Current situation: structured logs and performance summaries exist; checked-in alert policies do not.

Risk: a broken production provider or growing queue can remain unnoticed until users report it.

Recommendation: log-based metrics and a small dashboard for success/failure/latency, job age, provider 429s and readiness.

Why it helps: it turns existing signals into operational feedback without requiring distributed tracing first.

### Add a Redis integration test if Redis becomes production infrastructure

Current situation: route-cache logic has unit tests; production Cloud Tasks docs do not provision Redis.

Risk: configuration, serialization or TTL behavior could differ from the fake/unit assumptions.

Recommendation: run a disposable Redis integration test in CI or explicitly keep the cache optional and tested as a no-op.

Why it helps: it validates the actual operational dependency before making it required.

### Add load and cost-budget tests

Current situation: benchmark scripts record local aggregate timing and route/cache counters; there is no formal load test or per-session provider budget.

Risk: a new feature may multiply route or LLM calls unexpectedly.

Recommendation: add representative session benchmarks and assertions on maximum route calls, job duration and input fan-out.

Why it helps: it protects both performance and provider spend.

### Improve manual-input plausibility validation

Current situation: manual schemas enforce positive price/area and Smart Paste has warning ranges.

Risk: clearly implausible manual numbers can pass the API.

Recommendation: add domain warnings or validation for plausible floor area, lease year, price and Singapore listing identity, while allowing legitimate edge cases through explicit review.

Why it helps: it brings the two input paths closer together.

---

# 13. INTERVIEW-READY SYSTEM EXPLANATION

## 13.1 30-second explanation

NearHome makes reliability explicit at the data and workflow boundaries. It validates user and provider data, keeps provenance and unavailable states, separates immediate comparison from slower enrichment, and runs long work through durable jobs in production. It uses optional caching and bounded route concurrency to reduce latency and API pressure. The backend has tests for schemas, engines, provider parsing, jobs and browser workflows, and structured logs plus health/readiness checks help diagnose production problems. I would be careful to say that retries, alerts and tracing are partial rather than pretending every enterprise mechanism is present.

## 13.2 Two-minute explanation

At input time, Pydantic schemas validate request shapes and the Smart Paste path validates the LLM response again with a strict schema. OneMap results are checked for Singapore bounds and canonical HDB block/street agreement. HDB transactions are deduplicated and filtered to historical, positive, plausible records; model artifacts are checked for version, feature and snapshot compatibility. Values are stored with status, source, retrieval time, confidence and provenance.

When a provider fails, NearHome uses timeouts and provider-specific error classification. Geocoding failure marks that stage failed and leaves coordinate-dependent metrics unavailable, while immediate facts and some price/lease work can continue. Journey failures are recorded per destination/mode. Route errors are not silently changed into fake demo routes in live mode. Some transient failures can requeue a durable worker job, but there is no universal exponential-backoff or circuit-breaker implementation.

Enrichment is durable: the API creates a PostgreSQL job, production dispatches only its job ID through Cloud Tasks, and a private Cloud Run worker claims it atomically and persists progress. Local Docker can use Redis/ARQ, while inline mode is a local/test compatibility option. Optional Redis route caching, bounded concurrency and precomputed reference data reduce repeated work. Tests cover deterministic engines, schemas, adapters, data quality, workers, frontend helpers and E2E workflows. Structured request IDs, stage timing, job logs and health/readiness endpoints provide operational evidence, although there is no full tracing or alerting system in the repository.

## 13.3 Deep technical explanation

The architecture has separate contracts for transport, persistence and external providers. `RoutingProvider` returns canonical `RouteResult` objects. `EnrichmentService` orchestrates repositories and engines, while `EnrichmentRepository` stores per-field values and run statuses. A worker job is represented by `EnrichmentJobORM`; a partial unique index prevents two active session jobs, and `claim()` conditionally moves a queued row to running, so duplicate Cloud Tasks deliveries are safe at the database state boundary.

Data quality is layered. API Pydantic models enforce required shapes and basic ranges. Smart Paste has a strict JSON schema, content limits, deterministic candidate validation, confidence-based reconciliation and user confirmation. `LiveOneMapAdapter` enforces a broad Singapore coordinate envelope and exact canonical HDB block/street agreement when both sides expose identity. Google route parsing rejects missing/negative duration or distance instead of converting malformed data to zero. HDB/LTA/reference artifacts have deduplication, missing-data states and quality reports. Enriched values retain status, source, retrieval time, confidence, assumptions and provenance.

Failure handling is intentionally local where the meaning of the result can be preserved. The geocoding stage catches provider failures and marks only geocoding failed. Missing coordinates then prevent route-based metrics rather than generating geometry-based time. Journey matrix errors write temporary-unavailable journey estimates for each affected listing. Transport and driving rollups require minimum assessed coverage before contributing to recommendation. Unexpected exceptions can still escape a per-listing workflow and fail the job, which is a current improvement opportunity.

Performance is controlled rather than maximised blindly. The route cache is optional and only caches successful supported route-provider methods, with keys that include provider, method, rounded coordinates, mode, time bucket and preferences. A four-worker route pool deduplicates identical candidate calls and keeps failures attached to individual candidates. Google connections are reused per job. Static reference datasets and a prebuilt CatBoost artifact keep common work local. PostgreSQL commits are short and do not span slow provider calls.

Observability is structured logging rather than a full telemetry platform. Every request has a correlation ID. Worker logs include job/session/attempt/stage and safe error categories. Enrichment metrics record stage timings, route/cache counts and database timings into structured summary events. `/health` is process liveness; `/ready` checks PostgreSQL and conditionally Redis. There are no repository-defined alert policies or distributed traces, so production deployment still needs dashboards/alerts around job failures, queue age, provider errors and readiness.

---

# 14. INTERVIEW QUESTION BANK

## Data quality

**Question:** How do you ensure third-party data is trustworthy?

**Strong NearHome-specific answer:** I do not trust a provider response merely because it is valid JSON. I validate shape, semantic ranges and domain identity. For example, OneMap coordinates must fit a Singapore envelope and, when possible, match the listing's canonical HDB block/street. Route payloads must contain non-negative duration and distance. The result is stored with source, provenance, retrieval time, confidence and status.

**Likely follow-up:** What happens when validation fails?

**How I should answer:** The value is rejected or marked unavailable/insufficient, depending on the boundary. NearHome avoids substituting a fabricated number. Other independent stages can continue when their inputs remain valid.

**Question:** Why use canonical schemas?

**Strong NearHome-specific answer:** Provider payloads are unstable and provider-specific. `GeocodeResult` and `RouteResult` give engines a stable NearHome contract, so the scoring logic does not know Google's JSON field names or OneMap's keys.

**Likely follow-up:** Does a canonical schema guarantee correctness?

**How I should answer:** No. It gives shape and a stable vocabulary. Semantic validation and provenance are still needed.

**Question:** What is the difference between validation and normalisation?

**Strong NearHome-specific answer:** Normalisation makes equivalent representations agree, such as expanding `AVE` to `AVENUE`. Validation checks whether the normalised value is safe, such as requiring coordinates to fall inside Singapore.

**Likely follow-up:** Why not fuzzy-match every address?

**How I should answer:** HDB price evidence needs a defensible identity. The current address helper deliberately uses exact canonical block/street matching; fuzzy matching could attach a transaction from the wrong block or road.

## Failure handling

**Question:** What happens if OneMap goes down?

**Strong NearHome-specific answer:** The live adapter times out and raises; it has query variants but no general HTTP retry loop. `EnrichmentService` catches the geocoding failure, marks that run failed and leaves coordinates absent. Lease/fair-price work may continue, while transport, driving and school stages become unavailable rather than using a fake coordinate.

**Likely follow-up:** Would the whole comparison fail?

**How I should answer:** Immediate comparison can still return. Known geocoding failure is isolated. I would not claim every unexpected exception is isolated because the per-listing workflow still has a broader failure boundary to improve.

**Question:** What is the difference between retry, timeout and fallback?

**Strong NearHome-specific answer:** A timeout bounds how long a call can wait. A retry repeats the same call for a likely temporary failure. A fallback uses another method or source. OneMap query variants are not retries; CatBoost-to-weighted-comparables is a real fallback; the Playwright Smart Paste path is another retrieval method.

**Likely follow-up:** Does NearHome use exponential backoff?

**How I should answer:** The application code does not implement a general exponential-backoff policy. It has bounded provider-specific retries and Cloud Tasks retry classification.

**Question:** How do you prevent one provider failure from crashing an entire comparison?

**Strong NearHome-specific answer:** Known failures are contained at stage/component boundaries. Journey failures are saved per destination/mode, missing coordinates make only coordinate-dependent stages unavailable, and transport/driving rollups require coverage before contributing to recommendation. There is still a limitation: an unexpected exception in an unwrapped listing calculation can terminate the job.

## Caching and performance

**Question:** Why cache route results?

**Strong NearHome-specific answer:** Repeated routes are expensive in latency and potentially provider quota. The optional Redis cache keys by provider, method, rounded coordinates, mode, time bucket and preferences. It caches successful route results with TTLs that are shorter for traffic-aware driving.

**Likely follow-up:** Is every route matrix cached?

**How I should answer:** No. The newer route methods use the cache, but `get_route_matrix()` and the legacy important-location matrix adapter do not currently use the Redis route cache.

**Question:** Why not cache forever?

**Strong NearHome-specific answer:** A permanent answer becomes stale. NearHome uses 24 hours for walking, six hours for transit structure and 15 minutes for traffic-aware driving. HDB availability has a 60-second process cache and separately labels data older than 15 minutes stale.

**Likely follow-up:** What happens if Redis is unavailable?

**How I should answer:** The route cache degrades to a no-op and logs a safe warning. Correctness is preserved, but repeated provider calls may increase. Redis is required for local ARQ mode, not for Cloud Tasks/inline execution.

**Question:** How does NearHome reduce latency without making unlimited API calls?

**Strong NearHome-specific answer:** It reuses the Google HTTP client, deduplicates identical route keys, runs independent route candidates through a bounded pool defaulting to four, loads static/precomputed data locally, and keeps the listing loop sequential. That trades some throughput for provider and worker control.

## Workers

**Question:** Why use a worker instead of doing enrichment in the HTTP request?

**Strong NearHome-specific answer:** Enrichment can involve multiple slow providers and calculations. The API persists a job and returns 202/job status; production sends only the job ID through Cloud Tasks to a private Cloud Run worker. This shortens browser requests, makes progress durable and gives queue delivery a retry boundary.

**Likely follow-up:** Why have inline and ARQ modes too?

**How I should answer:** Inline is useful for local/test compatibility. Docker Compose uses Redis/ARQ to approximate a queued local flow. Production uses Cloud Tasks/Cloud Run so Redis is not a production requirement in the documented architecture.

**Question:** What if the same task is delivered twice?

**Strong NearHome-specific answer:** The active-job unique index prevents duplicate active session jobs, and `claim()` only updates a queued row. The first worker becomes running; the duplicate sees running/completed state and returns safely. Database upserts further prevent duplicate evidence rows. External calls are not guaranteed exactly once if a process dies at an unlucky moment.

**Question:** What happens if a worker crashes?

**Strong NearHome-specific answer:** Queue delivery can retry. A claimed job has durable timestamps; stale handling can requeue or fail abandoned work. Retryable exceptions are requeued up to the configured attempt limit; permanent failures are stored as failed with a safe user message.

## External APIs

**Question:** Why do not you call OneMap or Google directly from business logic?

**Strong NearHome-specific answer:** Adapters isolate provider URLs, authentication, field masks, response parsing and provider errors. Engines use canonical interfaces such as `RoutingProvider`, so mock/fixture/live implementations can be substituted without changing scoring logic.

**Likely follow-up:** How do you test provider behavior without calling the provider?

**How I should answer:** Tests use fake HTTP clients, mock adapters and deterministic fixtures. They can test 429s, malformed JSON, missing durations and valid responses without API cost or network flakiness.

**Question:** What protects API quotas?

**Strong NearHome-specific answer:** There is an application rate limit, bounded route concurrency, route-call deduplication, optional Redis cache, shortlist/input limits and static/precomputed reference data. The repository does not encode the providers' exact current quotas, so those must be checked operationally.

**Question:** What happens on Google 429?

**Strong NearHome-specific answer:** The routing adapter preserves HTTP status, provider error code, message and request ID in a `RoutingProviderError` marked retryable. The normal engine path can turn it into a provider-error/unavailable component; if it reaches the worker retry boundary, it can be requeued. It is not replaced with a fake live route.

## Testing

**Question:** What does your test suite actually verify?

**Strong NearHome-specific answer:** Backend tests cover schemas, address identity, Smart Paste, fair-price fallback/artifacts, transport/driving logic, route parsing/cache keys, parking quality, API integration and worker state transitions. Frontend tests cover API timeouts/progress/helpers and UI components. Playwright covers profile/listing/comparison, Smart Paste, enrichment, removal, fair-price and responsive workflows.

**Likely follow-up:** What is not covered?

**How I should answer:** There is no formal load test, full live-provider contract test, built-in alert test, distributed tracing test, circuit-breaker test or Redis multi-instance integration test. Database integration tests need a configured database.

**Question:** Why use fixtures and mocks?

**Strong NearHome-specific answer:** They make tests deterministic, fast, offline and free from provider quotas. They also let tests target failure cases deliberately. The provider-independent adapter contracts keep the fake behavior close to the production boundary.

**Question:** What is an E2E test compared with an integration test?

**Strong NearHome-specific answer:** An integration test such as `test_api_integration.py` combines API, repositories and database. A Playwright E2E test drives the user-facing web workflow. E2E tests may intercept selected APIs to stay deterministic; they are not automatically proof that every real provider works.

## Monitoring

**Question:** How would you know production is broken?

**Strong NearHome-specific answer:** I would check `/ready`, request IDs and structured logs, then inspect durable job status/progress, per-stage run states and worker/provider diagnostics. Enrichment logs already include stage timings, route/cache counts and database timings. I would add dashboards/alerts because the repository does not currently define them.

**Likely follow-up:** Is request ID the same as tracing?

**How I should answer:** No. It correlates logs for a request. I could not find OpenTelemetry or distributed tracing instrumentation.

**Question:** What is the difference between `/health` and `/ready`?

**Strong NearHome-specific answer:** `/health` shows the API process can answer. `/ready` checks PostgreSQL and conditionally Redis for ARQ mode. It does not call every external provider.

## Cost

**Question:** How do you prevent Google API costs from exploding?

**Strong NearHome-specific answer:** I bound route concurrency, deduplicate identical calls, use optional TTL-based route caching, use field masks and summary requests, limit the intended comparison fan-out, keep static data local, and record route request counts per job. I would verify actual quotas/prices from provider consoles rather than hard-code guesses.

**Likely follow-up:** What if retries make cost worse?

**How I should answer:** That is why retry classification and attempt caps matter. NearHome has bounded retries but no general backoff/circuit breaker yet; those are proportional improvements if live usage grows.

**Question:** Why precompute the CatBoost model?

**Strong NearHome-specific answer:** Training and calibration are expensive and unpredictable for an HTTP request. The Docker build or maintenance script produces an artifact; runtime validates and loads it. If it is incompatible, the system uses weighted comparable evidence or reports insufficient evidence rather than retraining during a user request.

---

# 15. THINGS I MUST PERSONALLY UNDERSTAND

## Must be able to explain without code

* Why provider adapters exist.
* What validation protects against.
* The difference between syntactically valid and semantically valid data.
* Why canonical block/street identity is safer than broad fuzzy matching for HDB evidence.
* What provenance, confidence and unavailable status mean.
* Retry versus timeout versus fallback versus cache.
* Why long enrichment work belongs in a durable worker flow.
* What a queue and job state are.
* What idempotency protects—and why it is not exactly-once external billing.
* Why caching reduces repeated work but creates freshness decisions.
* Why bounded concurrency protects both latency and provider quotas.
* Unit versus integration versus E2E tests.
* Why fixtures/mocks make tests deterministic.
* Logs versus metrics versus traces.
* What drives NearHome's API/worker/provider cost.

## Must be able to trace through the repository

* `POST /api/v1/sessions/{session_id}/enrichment/start` → `EnrichmentJobRepository` → selected executor.
* Cloud Tasks job ID → `worker_main.run_enrichment_task()` → atomic claim → `EnrichmentService` → PostgreSQL.
* Listing address → `LiveOneMapAdapter.geocode()` → `_is_usable_match()` → `GeocodeResult` → `enriched_fields`.
* Google provider response → `RouteResult` → transport/driving engine component → rollup.
* Route cache key construction → Redis get/set → TTL and no-op behavior.
* Smart Paste text/URL → retrieval/preparation → Groq schema → candidate validation/reconciliation → user confirmation.
* CatBoost artifact compatibility check → prediction or weighted-comparables fallback.
* A test for malformed provider data, a test for a duplicate worker task, and a test for a UI/API timeout.

## Good to know but not worth memorising

* Every FastAPI decorator argument.
* Exact SQLAlchemy boilerplate.
* Every environment variable.
* Individual fixture JSON rows.
* Every Playwright locator.
* The complete Google field mask string.
* Minor enum names that do not affect the architectural explanation.

The interview priority is being able to explain cause and effect and then locate the code that proves the claim.

---

# 16. GLOSSARY

**Adapter** — A boundary that translates a provider's API into NearHome's canonical interface. `LiveOneMapAdapter` maps OneMap JSON into `GeocodeResult`.

**Backoff** — Waiting between retry attempts. Exponential backoff increases the wait, for example 1s, 2s, 4s. The NearHome application does not currently implement a general exponential-backoff utility.

**Background job** — Work started by an API request but completed outside the request. NearHome enrichment is represented by an `enrichment_jobs` row.

**Cache** — Stored reusable output from an expensive operation. Optional Redis stores successful supported route results.

**Cache key** — The identifier used to find a cached result. NearHome's route key includes provider, method, rounded coordinates, mode and time bucket.

**Canonical schema** — A stable internal data shape independent of a provider. `RouteResult` is canonical; Google's raw route JSON is not.

**Circuit breaker** — A mechanism that temporarily stops calls to a repeatedly failing dependency, then probes it later. I could not find one implemented in the current repository.

**Confidence** — A signal about how strongly NearHome supports a value. Smart Paste candidates have model/final confidence; enriched fields store confidence.

**Defensive validation** — Checks designed to stop plausible-but-wrong data from propagating. Singapore coordinate bounds are an example.

**E2E test** — An end-to-end test that drives a realistic user workflow through the application. NearHome uses Playwright under `tests/e2e/`.

**Fallback** — A different method or source used when the primary path cannot provide a safe result. Weighted comparables are the fair-price fallback when CatBoost cannot predict.

**Fixture** — Stable test or demo data checked into the repository. HDB transactions and school/transport snapshots are fixtures.

**Graceful degradation** — Continuing with valid parts of a result while clearly marking a failed part unavailable. A journey-provider failure can leave other comparison sections usable.

**Health check** — An endpoint used to determine whether a process or dependency is alive. NearHome's `/health` is process-level; `/ready` checks required dependencies.

**Idempotency** — Repeating an operation does not create an unsafe duplicate effect. Conditional job claiming and active-job uniqueness provide idempotent durable job transitions.

**Integration test** — A test of multiple components together, such as FastAPI routes, repositories and PostgreSQL in `test_api_integration.py`.

**Logging** — Recording structured events about execution. NearHome logs JSON events with request IDs, job IDs and durations.

**Metrics** — Numeric measurements over time, such as failure count or latency. NearHome collects enrichment measurements and emits them in structured log summaries; it does not export a full metrics backend in the repository.

**Mock** — A controlled replacement for a real dependency. `MockRoutingProvider` avoids Google in demo/tests.

**Normalisation** — Converting equivalent representations into a stable form. `AVE` and `AVENUE` become the same canonical street spelling.

**Observability** — The ability to understand a running system from its outputs: logs, metrics, traces and related signals.

**Provenance** — Where a value came from and how it was produced. Examples are `OFFICIAL`, `ROUTED_LIVE`, `CALCULATED` and `MOCK_DEMO_DATA`.

**Quota** — A provider's allowed usage over a defined account/time/billing dimension. Exact provider quotas are not encoded in this repository.

**Queue** — A waiting line of jobs consumed by workers. Cloud Tasks and local ARQ/Redis provide queue mechanisms for enrichment.

**Rate limit** — A maximum number of requests allowed in a time window. NearHome's application rate-limit setting defaults to 60 requests per minute for configured API paths.

**Regression test** — A test that prevents a previously fixed failure from returning. The malformed-Google-duration test is a regression-style example.

**Retry** — Repeating the same operation after a likely temporary failure. NearHome retries HDB availability HTTP failures up to two attempts and requeues selected worker failures up to a configured attempt limit.

**Schema validation** — Checking that data has the required fields, types and allowed values. Pydantic validates API and Smart Paste schemas.

**Semantic validation** — Checking that a value makes sense for the domain, not only that it has the right type. A Singapore coordinate envelope is semantic validation.

**Stub/fake** — A small test implementation that returns controlled values or records calls. `FakeTasksClient` is used to inspect a Cloud Tasks payload without contacting Google.

**Task** — A unit of queued work. NearHome's Cloud Tasks payload contains an enrichment job ID.

**Timeout** — A maximum wait before abandoning an operation. Google route calls have configured httpx timeouts so a provider cannot hold a worker forever.

**Tracing** — Following one request across multiple services with spans and context. NearHome has correlation IDs but no repository-found OpenTelemetry tracing.

**TTL** — Time to live, the duration before a cache entry expires. A traffic-aware route cache entry lives 15 minutes by default.

**Unit test** — A test of a small component in isolation, such as a cache-key function or route parser.

**Unavailable state** — An explicit state meaning NearHome does not have safe usable evidence. It is different from a numeric zero.

**Worker** — A process that consumes background jobs. NearHome's production worker is a private Cloud Run FastAPI application; local ARQ uses `apps/api/app/jobs/worker.py`.

**Worker lease/stale job** — A practical time window used to decide whether a running job has stopped making progress. `ENRICHMENT_JOB_STALE_SECONDS` bounds abandoned work.

---

## Repository audit notes

The following discrepancies are worth remembering because they are good examples of why code inspection matters:

* `docs/data-sources.md` describes cache policies as planned, while route and availability caches are implemented only in specific code paths.
* The same document broadly says all adapters have timeouts, retries and caching; the code shows provider-specific behavior instead.
* `docs/testing-strategy.md` mentions `tests/integration/` as a Phase 2 location, but current API integration tests are under `apps/api/app/tests/` and no `tests/integration/` directory was found.
* `FallbackRoutingProvider` exists and is tested, but the normal live routing factory does not use it.
* `/health` is not a full dependency health check; `/ready` is the dependency-aware endpoint.
* The code has structured enrichment metrics in logs, but no checked-in Prometheus/OpenTelemetry/alert configuration.

The safest interview habit is to say exactly what the current code does, then label an improvement as an improvement rather than presenting it as already implemented.
