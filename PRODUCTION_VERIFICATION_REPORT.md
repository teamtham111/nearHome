# NearHome production verification report

## 1. Executive summary

**Overall production status: PASS WITH ISSUES**

NearHome's deployed Vercel frontend, Cloud Run API, Supabase database, live
provider integrations, persistence, core comparison workflow, and recommendation
calculation were exercised against production on 2026-08-06 (Asia/Singapore).
The complete supported journey works with real Singapore listing data: profile,
manual listings, text Smart Paste, live enrichment, journeys, comparison, and
recommendation all persisted and returned expected results.

It is safe to demonstrate as a portfolio product, with limitations. Smart Paste
text extraction works, while URL retrieval from PropertyGuru is correctly handled
as unavailable when that site blocks access.  The named-school reference data also
does not currently resolve every valid Singapore school.  The main availability
issue is inline enrichment: it can consume the sole Cloud Run request slot and
make concurrent status requests return 429. A temporary concurrency-two test was
not durable, so it was reverted; this remains an unresolved high-severity
deployment architecture limitation.

No secret values were displayed or committed.  No application source files were
changed during this QA pass.

## 2. Environment tested

| Item | Value |
| --- | --- |
| Frontend | `https://nearhome.vercel.app` |
| API | `https://nearhome-api-yxob3mwuca-as.a.run.app` |
| Test date/timezone | 2026-08-06, Asia/Singapore |
| Git commit | `2658bbd0` |
| Browser | Chromium/Playwright, desktop 1440x900 and iPhone 13-sized 390x664 viewport |
| Services | Vercel, Cloud Run, Supabase PostgreSQL, Google Routes, Google Places, OneMap, Groq, Singapore reference datasets |
| Background mode | Inline enrichment (`JOB_EXECUTION_MODE=inline`); Redis/ARQ is not used by this production deployment |

Production configuration was inspected by variable name/presence only.  The API
has server-side database, Google, OneMap and Groq credentials configured; Vercel
has only the intended public API/deployment variables plus server-only OneMap
credentials.  No frontend bundle exposed a recognised API-key, database URL,
Groq key, OneMap password, or service-role identifier.  `/.env` returns 404 and
the deployed browser JavaScript contains no localhost URL references.

## 3. Test results

| Area | Test | Result | Evidence | Notes |
| --- | --- | --- | --- | --- |
| Frontend | Production landing page and assets | PASS | HTTPS 200; nine referenced JS assets loaded | No blank screen observed. |
| Frontend | Desktop/mobile navigation and refresh | PASS | Playwright completed landing, session creation, manual listings, refresh and direct nested comparison route | No console errors, failed requests, CORS errors, or localhost calls. |
| API | Health/readiness | PASS | `/api/v1/health` and `/api/v1/ready` returned 200 and database `ok` | Readiness correctly reports Redis as `not_required`. |
| API | Routes and error contract | PASS | OpenAPI exposes 21 expected paths; malformed UUID 422, unknown session 404, invalid profile 422, duplicate listing 409 | JSON responses were structurally valid. |
| CORS | Allowed and untrusted origins | PASS | Vercel-origin OPTIONS returned exact allow-origin; untrusted origin returned 400 without allow-origin | CORS is not wildcard. |
| Database | Connection/schema/migrations | PASS | Cloud Run writes/read persisted; Alembic `008_raw_listing_subtype (head)`; 18 required public tables | RLS enabled on all 18 public tables. Policy behaviour was not independently audited because browser clients do not access Supabase directly. |
| Sessions | Create/update/reload/delete | PASS | API CRUD plus a new browser context and page refresh retained sessions/listings | Deleted session returns 404. |
| Manual listing | Create, duplicate rejection, remove | PASS | Two realistic HDB listings added; duplicate 409; a third test listing and observation removed without affecting the other listings | Immediate comparison exposed ten available metrics before enrichment. |
| Smart Paste text | Groq extraction and discard | PASS | Realistic text created a draft with address, price, type/subtype, area and lease fields; discard succeeded | Groq runs server-side. |
| Smart Paste URL | PropertyGuru page retrieval | PARTIAL | A valid PropertyGuru URL returned clear 422 `LISTING_PAGE_UNAVAILABLE` | The page was blocked/unavailable to the server. Text paste remains the supported recovery path. Successful Playwright fallback against a blocked site was not proven. |
| URL safety | Internal URL rejection | PASS | `http://127.0.0.1:8000` was rejected with 400 `INVALID_LISTING_URL` | Confirms SSRF guard without attempting exploitation. |
| OneMap | Live address search | PASS | Vercel server route returned eight Raffles Place MRT suggestions with coordinates | Credentials remain server-only. |
| Google Places | Autocomplete | PASS | Bishan MRT request returned five live suggestions | Provider response parsed by backend. |
| Google Routes | Enrichment and destination routes | PASS | Provider-marked `ROUTED_LIVE` transport/driving results; a weekday 08:00 Raffles Place test returned DRIVE 22 min and TRANSIT 45 min | Live Google routes, not mock values. |
| Fair price | Transactions and estimate | PASS | Bishan estimate S$817,186 from 136 eligible transactions; Tampines estimate S$777,822 from 3,132 | Both had deliberately visible LOW-confidence evidence, not false precision. |
| Transport | Component calculation | PASS | Both listings produced Access, Bus coverage, MRT reach and Route resilience with full coverage | Scores and evidence matched provider/source labels. |
| Driving | Component calculation | PASS | Major-road, connectivity, peak penalty and parking components all calculated from live/reference sources | Destination omitted correctly when profile had none. |
| Schools | Distances and named schools | PARTIAL | Distance/count evidence returned; Catholic High matched; valid Raffles Institution was not found in school reference snapshot | Reference-data coverage limitation; see issues. |
| Requirements | Pass, one fail, multiple fail | PASS | Area >=100: Bishan FAIL/Tampines PASS; area >=110: both FAIL and group `FAILS_MULTIPLE` | Existing requirement engine behaves as intended. |
| Recommendation | Eligibility and weighted scoring | PASS | Tampines recommended under saved affordability/fair-price/transport priorities; changing requirements changed eligibility | Manual trace below agrees with API response. |
| Security | Browser exposure/error handling | PASS | No recognised secret patterns in deployed JS, `/.env` 404, production diagnostic route 404, invalid input gave safe JSON | Server logs use structured request IDs; production hides provider bodies/stack traces. |
| Production smoke | Reusable read-only script | PASS | `FRONTEND_URL=... BACKEND_URL=... scripts/production-smoke.sh` passed | Script checks frontend, health, ready, and CORS without paid calls. |
| Redis/ARQ | Queued-worker execution | NOT TESTED | Production ready endpoint explicitly reports inline mode and Redis not required | It is intentionally not part of this Cloud Run deployment. |
| Database failure simulation | Forced provider/database outage | NOT TESTED | Not forced against production | Safe validation/error routes were tested; no production database interruption was introduced. |

## 4. Full user journey result

1. The production landing-page call to action opened a new persisted comparison
   session. Direct deep-linking to its nested comparison route and refreshing it
   worked on both desktop and mobile-sized Chromium.
2. A buyer profile with a S$900,000 budget, three priorities, `BOTH` transport
   mode, and two named schools was saved and read back through the comparison API.
   Optional fields being absent did not prevent comparison.
3. Two meaningful real Singapore HDB addresses were entered manually: 217 Bishan
   Street 23 (4 Room/4A, 92 sqm, S$820,000) and 201 Tampines Street 21 (4 Room/4A,
   104 sqm, S$610,000). The API correctly rejected a duplicate.
4. Before enrichment, budget/area/lease/storey and other immediate metrics were
   available; fair price was correctly labelled as awaiting enrichment rather than
   zero.
5. Live enrichment completed successfully for eight categories per listing:
   geocoding, lease, fair price, public transport, driving access, schools,
   transaction data and property data. There were no failed or partial runs.
6. A separate profile with an OneMap-validated Raffles Place MRT destination ran
   both `IMPORTANT_LOCATION_DRIVING` and `IMPORTANT_LOCATION_PT` successfully.
   The persisted route results were 22 minutes driving and 45 minutes public
   transport for weekday 08:00.
7. The recommendation selected Tampines as the strongest eligible option and
   explained its affordability/fair-price advantages. Requirement changes moved
   Bishan to FAIL while preserving Tampines as eligible; a stricter requirement
   made both fail.

All sessions created for this QA run were deleted. Because the current delete
endpoint detaches confirmed listings before deleting a session, four exact
QA-labelled listing rows were then safely deleted by their unique display names;
their child enrichment rows cascaded. Follow-up checks found zero current-run
listing inputs, extraction attempts, or enrichment runs. Four older,
pre-existing `E2E_TEST_` listings were deliberately left untouched.

## 5. Database verification

Cloud Run connected successfully to Supabase PostgreSQL through the configured
server-side connection string. Alembic reported `008_raw_listing_subtype (head)`.
The production schema has the 18 expected public tables, including sessions,
listings, profiles, enrichment, journeys, observations and recommendation traces.

API create/read/update/delete checks persisted across independent requests and a
fresh browser context. UUID, JSON/JSONB and nullable values in the exercised
responses serialized correctly. No deployed-schema mismatch was detected. RLS is
enabled on all public tables; since all production browser data access is via the
API rather than a Supabase client, client-side RLS policy access was not exercised.

## 6. Integration results

| Integration | Configuration/authentication | Result | Fallback and user-facing behaviour |
| --- | --- | --- | --- |
| Supabase PostgreSQL | Present, server-side | PASS | API persisted and read all tested data. |
| Google Routes | Present, server-side | PASS | Live routed transport/driving components and destination journeys returned. Development diagnostic endpoint is intentionally absent in production. |
| Google Places | Present, server-side | PASS | Autocomplete returned live suggestions. |
| OneMap | Present on Vercel server | PASS | Address suggestions returned live; invalid empty query returns safe 400. |
| Groq | Present, server-side | PASS | Text Smart Paste extracted structured candidate fields. |
| PropertyGuru retrieval | External site availability | PARTIAL | Safe clear 422 when site cannot be accessed; no crash or fabricated data. |
| Singapore reference datasets | Available in enrichment | PASS WITH LIMITATION | Transport/driving/transactions/carpark evidence calculated; school snapshot lacks some named institutions. |
| Redis/ARQ | Not configured for deployed mode | NOT APPLICABLE | Inline execution is the intentional replacement. |
| Playwright/Chromium retrieval fallback | Packaged code path | NOT TESTED | URL test failed before a successful fallback could be established. |

## 7. Scoring verification

The API returned scores on a 0–100 scale. For the saved profile, priorities and
normalised weights were Affordability 0.45, Fair Price 0.35 and Public Transport
0.20. The backend's weighted total is:

`total = sum(component_score × priority_weight)`

For Bishan, the returned components were Affordability 67.78, Fair Price 49.14,
and Public Transport 81.90:

`(67.78 × 0.45) + (49.14 × 0.35) + (81.90 × 0.20) = 64.077 ≈ 64.08`

For Tampines, the returned components were 100.00, 100.00, and 80.80:

`(100 × 0.45) + (100 × 0.35) + (80.80 × 0.20) = 96.16`

This matches the persisted API totals (64.08 and 96.16) and explains the Tampines
recommendation. Fair-price raw values were confidence-adjusted before conversion:
Bishan's low-confidence raw gap was -0.00172176, producing 49.14 rather than
being represented as an unscaled fraction. All priority metrics had coverage 1.0,
so no renormalisation was needed for this trace.

## 8. Issues found

### High — inline enrichment can temporarily block concurrent API requests

**Affected component:** Cloud Run deployment/reliability.

**Reproduction:** Start live inline enrichment while Cloud Run
was configured with `containerConcurrency=1` and `maxScale=1`, then request
`/enrichment/status`. The status call returned HTTP 429 while the long live
enrichment request held the only request slot.

**Expected:** A user can poll progress and use lightweight routes while enrichment
is running.

**Root cause:** synchronous inline execution plus one Cloud Run request slot.

**Temporary mitigation tested and reverted:** Cloud Run was briefly set to
`containerConcurrency=2` while retaining `maxScale=1`. A status request initially
returned 200 during the run, but a later status request still returned 429. The
setting was restored to `containerConcurrency=1` (revision
`nearhome-api-00004-wt5`) rather than presenting an unreliable change as a fix.

**Required fix:** move enrichment to a durable external worker/queue, or redesign
the API execution model so the initiating request returns promptly and progress
is persisted. This needs an architecture/cost decision; do not increase scaling
blindly. Regression status: not fixed; reproduction remains confirmed.

### Medium — PropertyGuru URL Smart Paste is not consistently retrievable

**Affected component:** Smart Paste URL retrieval.

**Reproduction:** Submit
`https://www.propertyguru.com.sg/listing/hdb-for-sale-217-bishan-street-23-60027295`.

**Actual:** HTTP 422, `LISTING_PAGE_UNAVAILABLE`, with an instructive message to
copy listing details and paste text instead.

**Root cause:** the external listing site did not permit the server retrieval in
this production request. This is not treated as extracted data or a server crash.

**Fix:** none applied; text Smart Paste remains fully working. A future change
should only be made after confirming a permitted retrieval method with the source
site. Regression status: failure path verified; successful blocked-page Playwright
fallback not verified.

### Medium — named-school snapshot has incomplete coverage

**Affected component:** school enrichment/reference data.

**Reproduction:** select Catholic High School and Raffles Institution. OneMap
finds Raffles Place suggestions and the profile accepts the school name, but the
school evidence reports `Named school not found in reference snapshot: Raffles
Institution`.

**Expected:** valid supported named schools should resolve consistently.

**Root cause:** the scoring reference snapshot does not contain a matching
Raffles Institution entry (likely scope/normalisation coverage), while the live
address selector is a broader OneMap source.

**Fix:** none applied during QA because expanding/changing the official school
reference dataset requires a deliberate data-scope decision. Regression status:
matched and unmatched states both render safely.

### Low — session deletion leaves detached confirmed listings

**Affected component:** test-data hygiene/session deletion semantics.

**Actual:** deleting a session returned 204 but detached rather than removed its
confirmed listings. This was confirmed by the nullable `session_id` rows.

**Mitigation applied:** only the four exact QA-labelled listing rows were deleted
after foreign-key inspection; dependent enrichment data cascaded.

**Recommendation:** decide whether normal session deletion should preserve a
shortlist intentionally. If not, change the endpoint/repository transaction and
add a regression test. No product behaviour was changed in production during QA.

### Low — client-aborted enrichment request needs a staging regression test

**Affected component:** Cloud Run request-abort handling.

**Evidence:** Cloud Run recorded one HTTP 500 for the controlled enrichment start
request after the QA command intentionally stopped its HTTP client early. The log
had no application error payload. Separate normal live enrichment requests
completed with HTTP 200 and persisted their results.

**Status:** not treated as a confirmed user-flow failure because the request was
deliberately interrupted by the test harness. Reproduce a browser/network-abort
case in staging and ensure cancellation returns a safe, retryable response without
leaving an in-memory active-session lock.

## 9. Code changes made

No application source code was modified in this verification pass.

This report was added as `PRODUCTION_VERIFICATION_REPORT.md`. A temporary Cloud
Run concurrency experiment was reverted after it proved unreliable, so the final
production configuration is unchanged. The existing read-only
`scripts/production-smoke.sh` was run successfully; it was already present and
was not modified.

## 10. External deployment actions required

No mandatory dashboard action is required to demonstrate the verified workflow.

Recommended follow-ups:

- **Cloud Run/worker hosting:** retain the present single-concurrency setting until
  a deliberate asynchronous job design is deployed. If users commonly run
  enrichments, deploy a dedicated Redis/ARQ worker or another durable job
  executor, then move the API to queue work and return promptly.
- **Supabase:** decide whether detached confirmed listings after session deletion
  are intended, then implement/document the chosen retention policy. Review RLS
  policies before adding any direct browser Supabase access.
- **School data pipeline:** refresh/extend the official-school reference snapshot
  and normalisation aliases to cover the supported named-school scope.
- **Third-party listing sites:** do not weaken SSRF controls or bypass site
  restrictions. Treat text paste as the recovery path unless an approved,
  reliable integration becomes available.
- **Vercel:** no required setting change. Keep OneMap credentials server-only and
  public variables limited to `NEXT_PUBLIC_*` values intentionally exposed.

## 11. Remaining limitations

- A successful URL Smart Paste fallback through Playwright/Chromium was not
  demonstrated against a blocked external listing site.
- Redis/ARQ queue/worker behaviour was not tested because this deployment is
  explicitly inline and does not require Redis.
- Database outage, malformed third-party payload, permanent cold-start timing,
  ties, and a true missing-data requirement case were not forced against
  production to avoid damaging service availability or consuming unnecessary
  provider quota.
- School reference coverage is incomplete for at least Raffles Institution.
- Four E2E-labelled listings pre-dated this QA run and were not touched.
- A deliberately client-aborted enrichment generated one Cloud Run 500 log entry;
  its normal-user impact is unconfirmed and should be regression-tested in staging.

## 12. Final production checklist

- [x] Frontend loads; desktop and mobile browser workflow works.
- [x] Backend HTTPS, health, readiness, OpenAPI routes and CORS work.
- [x] Supabase connection, migrations, persistence and API CRUD work.
- [x] Production API base URL is used; no deployed browser localhost references.
- [x] Smart Paste text extraction works server-side.
- [~] URL Smart Paste has a safe, clear failure path; blocked-site success remains unverified.
- [x] Live fair-price, transport, driving, Google Routes and destination journeys work.
- [~] School scoring works but named-school reference coverage needs expansion.
- [x] Requirements and recommendation calculations were traced and agree with API totals.
- [~] Inline enrichment completes, but concurrent status can return 429; durable asynchronous execution is required for robust progress polling.
- [x] Browser and API errors were safe JSON without secrets or stack traces.
- [x] Production-safe smoke script passes.
- [x] Current-run temporary data was cleaned without modifying unrelated records.
- [~] Redis/ARQ not applicable to the present inline deployment.

```text
Production verdict: PASS WITH ISSUES

Safe for portfolio demonstration: YES WITH LIMITATIONS

Blocking issues:
- None for the verified manual-listing and text-Smart-Paste workflow.

Recommended next action:
- Adopt a durable background worker/queue for enrichment, then expand the named-school reference dataset.
```
