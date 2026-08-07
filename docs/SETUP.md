# NearHome Setup Guide

Everything you need to run NearHome locally, optionally connect live data, and reach full-spec production quality.

---

## Quick start (demo mode — no API keys)

Demo mode works out of the box with mock/fixture data. No downloads required.

### Without Docker (Homebrew PostgreSQL — recommended on Mac)

```bash
# From repo root
cd /Users/teamtham/nearhomev2
cp .env.example .env   # skip if .env already exists

# One-time: install and start Postgres
brew install postgresql@16
brew services start postgresql@16

# One-time: create DB user/database (superuser = your Mac login)
/opt/homebrew/opt/postgresql@16/bin/psql postgres -c "CREATE USER nearhome WITH PASSWORD 'nearhome' CREATEDB;" 2>/dev/null || true
/opt/homebrew/opt/postgresql@16/bin/psql postgres -c "CREATE DATABASE nearhome OWNER nearhome;" 2>/dev/null || true

# Terminal 1 — API
cd apps/api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# Terminal 2 — Web (new terminal tab)
cd apps/web
npm install
npm run dev
```

Run **one command per line**. Do not paste `# comment` suffixes — zsh will break uvicorn.

If you see `Address already in use` on port 8000, an API instance is already running — open http://localhost:3000 instead of starting another.

If Smart Paste shows `Load failed`, inspect the API terminal first. The API must be started
from `apps/api/.venv` (as above), because live Smart Paste requires the installed `groq` and
`playwright` packages. Restart the API after activating the environment; otherwise a system
Python process can return a generic browser fetch error while logging `ModuleNotFoundError`.

### With Docker

```bash
# 1. Copy environment file
cp .env.example .env

# 2. Start Postgres + Redis (Docker)
docker compose up -d postgres redis

# 3. Run database migrations
cd apps/api
pip install -e ".[dev]"
alembic upgrade head

# 4. Start API (terminal 1)
uvicorn app.main:app --reload --port 8000

# 5. Start web (terminal 2)
cd ../web
npm install
npm run dev
```

The web scripts automatically load the repository root `.env` for server-side
Next.js routes, including OneMap address search. Keep OneMap credentials in the
server-side `ONEMAP_EMAIL` and `ONEMAP_PASSWORD` variables, never in a
`NEXT_PUBLIC_*` variable.

Open http://localhost:3000 → create a session → add 2 listings → save profile → **Run enrichment** → view comparison.

Saved important locations retain their confirmed address, coordinates, travel mode, day, and
departure time when the profile is reopened, so updating another profile field does not erase the
destination used for journey estimates.

Before running `npm run build`, stop any active Next.js development server first. The build
rewrites `.next`; leaving `next dev` running can make the dev server serve a partial build and
return a 500 with a missing `_document.js`. Restart `npm run dev` after the build completes.

Or use the helper script:

```bash
./scripts/start-local.sh
```

---

## What demo mode gives you

| Feature | Demo source |
| --- | --- |
| Geocoding | Mock OneMap (town from address text) |
| Smart Paste | Mock Groq extraction |
| Fair price | Fixture HDB transactions (`data_pipeline/fixtures/hdb_transactions.json`) |
| Public transport | LTA/MRT fixture snapshots + geocoded coordinates |
| Driving access | Coordinate-based heuristic |
| Schools | MOE school fixture snapshot |
| Journey times | Mock Google Routes matrix |

Set `DEMO_MODE=true` in `.env` (default).

---

## Optional: live API keys

Set `DEMO_MODE=false` in `.env` and add the provider keys below. Live adapters fail clearly when a required key is missing; they never silently substitute demo data.

### 1. OneMap (geocoding)

1. Register at https://www.onemap.gov.sg/apidocs/register
2. Add to `.env`:
   ```
   ONEMAP_EMAIL=your@email.com
   ONEMAP_PASSWORD=yourpassword
   ```

### 2. Google Maps Platform (Places + Routes)

1. Create a GCP project → enable **Places API** and **Routes API**
2. Create an API key with appropriate restrictions
3. Add to `.env`:
   ```
   GOOGLE_MAPS_API_KEY=AIza...
   ```

Used for: journey route matrix. Important-location address search uses OneMap.

### 2b. Official HDB carpark data and availability

Refresh the static [HDB Carpark Information dataset](https://data.gov.sg/datasets/d_23f946fa557947f93a8043bbef41dd09/view)
before a production data refresh:

```bash
python data_pipeline/ingest_hdb_carparks.py --live --persist-db
cd apps/api && alembic upgrade head
```

This is an explicit maintenance/import operation, not part of **Run
enrichment**. See [enrichment performance operations](ENRICHMENT_PERFORMANCE.md)
for its idempotent behaviour, optional Redis route caching and CatBoost artifact
build command.

The live [HDB Carpark Availability API](https://data.gov.sg/datasets/d_ca933a644e55d34fe21f28b8052fac63/view)
is read server-side. Set `DATA_GOV_SG_API_KEY` if using a data.gov.sg API
key, and adjust `HDB_CARPARK_AVAILABILITY_CACHE_SECONDS`,
`HDB_CARPARK_AVAILABILITY_STALE_MINUTES`, or
`HDB_CARPARK_HISTORY_MIN_SAMPLES` only when operational policy requires it.
Never put either provider key in a `NEXT_PUBLIC_*` variable.

Collect historical snapshots separately from user comparisons, for example
with a scheduler every 5–15 minutes:

```bash
python data_pipeline/ingest_hdb_availability.py
```

To diagnose the primary `computeRoutes` request locally without the mock
fallback, keep `APP_ENV=development` and call:

```bash
curl -sS -X POST http://localhost:8000/api/v1/diagnostics/google-routes \
  -H 'Content-Type: application/json' \
  -d '{"origin":[1.3521,103.8198],"destination":[1.3009,103.8563],"travel_mode":"DRIVE"}'
```

The response identifies the HTTP status and Google error code without
returning the API key. The API process and the ARQ worker must both be
restarted after changing routing code or environment variables; the worker
does not hot-reload imported provider modules.

For live driving requests, Google requires a traffic-aware routing preference
when a departure timestamp is supplied. Non-traffic-aware driving requests
omit the timestamp.

### 3. Groq (Smart Paste)

1. Get an API key from the Groq console
2. Add to `.env`:
   ```
   GROQ_API_KEY=...
   GROQ_MODEL=openai/gpt-oss-20b
   ```

Smart Paste also accepts PropertyGuru and 99.co Singapore listing URLs. NearHome retrieves and
sanitises the public page on the API server before sending bounded listing evidence to Groq. An
access-denied, CAPTCHA or JavaScript-shell response triggers a bounded headless-browser retry when
appropriate; an unusable page still returns a copy-and-paste fallback. Normal Cloudflare CSP
references on a successfully rendered listing are not treated as a block, and no CAPTCHA or
access-control bypass is attempted.

The web app allows Smart Paste up to two minutes to complete because URL retrieval and a bounded
headless-browser fallback can run before Groq extraction. Other interactive API calls keep their
shorter timeout, so a slow Smart Paste request does not change ordinary form behaviour.

If Groq rejects a generated object for omitting required JSON-schema fields, NearHome makes
one bounded retry with an explicit schema-repair instruction. In development, any remaining
provider failure includes the provider HTTP status and safe provider message under
`detail.providerMessage`; the web UI includes that message so schema, quota or authentication
problems are distinguishable from a generic network failure. Production responses retain the
generic error and never return provider response details or credentials.

Before confirming an extraction, use **Discard listing** if the wrong URL or text was pasted.
NearHome clears the review form and removes the unconfirmed extraction draft; no listing is added
to the shortlist.

When **Run enrichment** is active, NearHome starts at 0% and derives progress only from durable
backend run statuses. It shows weighted completion for geocoding, property and lease data,
transaction data, schools, fair price, public transport and driving access; a running check does
not contribute progress. Optional unavailable checks are labelled as such, rather than as ready.
At terminal completion, the compact progress card replaces its running message with a success
summary; the comparison page does not duplicate this with a separate eight-badge status card.

### 4. LTA DataMall (full bus network — optional)

1. Register at https://datamall.lta.gov.sg/
2. Add to `.env`:
   ```
   LTA_ACCOUNT_KEY=...
   ```
3. Refresh bus stop fixtures:
   ```bash
   LTA_ACCOUNT_KEY=... python data_pipeline/ingest_lta_reference.py --live
   # Optional (slow): attach services per stop
   LTA_ACCOUNT_KEY=... python data_pipeline/ingest_lta_reference.py --live --with-services
   ```

Without live LTA data, the bundled fixture covers Bishan, Tampines, Jurong East, and Punggol demo areas.

---

## Data downloads & ingest

### HDB resale transactions (fair price)

1. Download CSV from [data.gov.sg HDB Resale Flat Prices](https://data.gov.sg/dataset/resale-flat-prices)
2. From the **repo root** (`nearhomev2/`, not `apps/api/`):

   ```bash
   cd /Users/teamtham/nearhomev2
   source apps/api/.venv/bin/activate
   python data_pipeline/ingest_hdb_transactions.py "/path/to/Resale flat prices....csv"
   ```

   All rows (2017+):

   ```bash
   python data_pipeline/ingest_hdb_transactions.py "/path/to/file.csv" --min-month ""
   ```

   Recent only (default, from 2022):

   ```bash
   python data_pipeline/ingest_hdb_transactions.py "/path/to/file.csv"
   ```
3. Output: `data_pipeline/fixtures/hdb_transactions.json`

More rows → better comparable coverage for fair-price estimates.

### MRT stations

Bundled at `data_pipeline/fixtures/mrt_stations.json`. Extend manually or add a OneMap/LTA ingest script for production.

### MOE schools

Bundled at `data_pipeline/fixtures/moe_schools.json`. For production, download MOE school directory and extend the fixture.
The buyer profile accepts up to 10 named schools and reports each selected school's distance separately. New school selections use the same server-side OneMap search as important locations: users must choose a confirmed school-like Singapore result rather than saving free text. Older clients sending `named_school` remain compatible.

---

## Fair-price data maintenance

Fair price uses a prebuilt CatBoost artifact as the primary estimator. After HDB
transactions are ingested, rebuild the artifact with the command in
[enrichment performance operations](ENRICHMENT_PERFORMANCE.md); runtime validates
the transaction snapshot and never fits or calibrates during a user request.
Weighted comparables still provide buyer-visible evidence and are the explicit
fallback if the artifact cannot load or validate. Lease is resolved once by the
canonical estimator and stored internally as months: official exact value, recent
exact-block transaction expiry median, commencement-year fallback, then
low-confidence listing text. Missing/invalid transaction lease values remain
missing; they are never replaced with a default such as 65 years. A remaining
lease of zero is treated as missing; the listing must have a positive canonical
lease for valuation.

For local development, run `./scripts/train-fair-price-model.sh` from the
repository root once after setting up the API environment, and set
`FAIR_PRICE_MODEL_ARTIFACT_PATH=artifacts/fair_price/catboost` in the root
`.env`. The directory is generated locally and intentionally ignored by Git.
Restart the API and ARQ worker after rebuilding it. Neither process trains a
model while serving a listing or an enrichment job.

The buyer-facing valuation card translates this response into a central estimate,
likely range, asking-price comparison, confidence explanation, property details,
up to ten strongest recent contextual comparables and supported limitations. Raw model metadata and
filter diagnostics remain in the API for developers but are intentionally hidden
from ordinary users; see [docs/fair-price-ui.md](fair-price-ui.md).

Town and lease matching share the canonical block/street utility in
`apps/api/app/utils/hdb_address.py`. Town uses an authoritative geocoder value
first, then an exact historical transaction match only when that value is
absent. Run the separate full-dataset challenger benchmark with
`python -m app.evaluation.run_model_benchmark --mode full`; it writes reports
under `evaluation_outputs/` and does not change runtime weights or hyperparameters.

Enrichment also persists the resolved canonical lease months, derived years, source, confidence and as-of date on the confirmed listing, so the session API and UI expose the same value used by fair-price estimation.

Smart Paste retains a compact listing subtype as `raw_listing_subtype` (with
`listing_flat_subtype` retained as a compatibility alias), then deterministically
derives missing canonical HDB fields. For example, `4A` becomes `4 ROOM` plus
`Model A`, and `5STD` becomes `5 ROOM` plus `Standard`. Known mappings include
`A`, `A2`, `NG`, `S`, `I`, `STD` and `PA` suffixes for 2–5-room codes, plus
`EA` → `EXECUTIVE`/`Apartment` and `EM` → `EXECUTIVE`/`Maisonette`.
Unknown or suffix-only values remain unmapped rather than receiving a guessed model.
User-confirmed values take precedence over extracted values, which take precedence
over subtype-derived values; disagreements are retained as review evidence.
Storey range is an optional confirmation-form field and is always blank
after Smart Paste; only a user selection can populate it. The fair-price response
includes actual filter status, stage counts, exact relaxation steps and
non-duplicative info/warning messages.

The listing confirmation API rejects non-positive remaining-lease values. Legacy zero values are normalized to missing before persistence. Expanded comparison evidence includes the estimate source, confidence and as-of date.

---

## Worker (async enrichment)

For background enrichment via Redis/ARQ:

```bash
docker compose up -d redis
cd apps/api && python -m app.jobs.worker
```

`POST /api/v1/sessions/{id}/enrichment/start` returns a durable job reference.
Local inline mode completes it synchronously for compatibility; production mode
uses Cloud Tasks and a private worker. Poll `GET /api/v1/jobs/{job_id}?session_id={session_id}`
for stored job status without triggering work.
Before a queued run starts, prior enrichment rows are marked `QUEUED`, so the browser
does not mistake results from an earlier run for completion of the current run. The
browser waits for the current run and reports failed enrichment steps instead of treating
them as success; retry **Run enrichment** after correcting the provider or configuration.

Full stack with Docker:

```bash
docker compose up --build
```

---

## Tests

```bash
# Backend unit tests
cd apps/api && pytest -q

# Integration (needs Postgres)
DATABASE_URL=postgresql+psycopg://nearhome:nearhome@localhost:5432/nearhome pytest app/tests/test_api_integration.py -q

# Frontend
cd apps/web && npm test && npm run typecheck

# E2E (API + web running)
cd tests/e2e && npm install && npx playwright install chromium && npm test
```

The Smart Paste E2E scenario supplies sample listing text before clicking **Add a flat**; the button is intentionally disabled for an empty paste.

To run E2E tests alongside an already-running local stack, use isolated ports. The test runner
starts the API with the matching CORS origin and the web app with the matching public API URL:

```bash
cd tests/e2e
WEB_URL=http://localhost:3001 API_URL=http://localhost:8001 npx playwright test
```

`SKIP_ROOT_ENV=1` is used internally by this isolated E2E path only. Normal `npm run dev`
continues to load the root `.env` file.

---

## Full spec checklist — what still needs your action

| Spec area | Built in code | You must provide |
| --- | --- | --- |
| Immediate comparison | ✅ | Nothing |
| Smart Paste + confirmation | ✅ | `GROQ_API_KEY` for live LLM |
| Fair price (baseline) | ✅ | HDB CSV ingest for real comparables |
| Fair price (CatBoost + comparable evidence) | ✅ | HDB CSV ingest for refreshed training transactions |
| Public transport (5 components) | ✅ from snapshots | LTA live ingest for island-wide coverage |
| OneMap pedestrian routing to MRT | 🔄 haversine walk | OneMap routing API for precise walk times |
| Driving (peak sampling) | 🔄 heuristic | Google Routes repeated samples for production |
| Important-location address search | ✅ | `ONEMAP_EMAIL` / `ONEMAP_PASSWORD` |
| Important-location journeys | ✅ | `GOOGLE_MAPS_API_KEY` |
| Schools | ✅ from fixture | MOE full dataset for production |
| Observations | ✅ API + UI display | Nothing |
| Session deletion | ✅ | Nothing |
| Rate limits (Smart Paste) | ✅ basic | Tune limits in `app/core/rate_limit.py` |
| Recommendation audit trace | ✅ API | Nothing |
| WCAG AA full audit | 🔄 partial | Manual accessibility review |
| OpenAPI typed client | ⏳ | Run codegen from `/openapi.json` if desired |
| Non-HDB blocking | 🔄 schema | Enforce in UI when past demo |

---

## Environment reference

See `.env.example` for all variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Yes (non-docker local) | PostgreSQL connection |
| `REDIS_URL` | Optional | ARQ worker queue |
| `DEMO_MODE` | No (default true) | Use mock adapters |
| `GROQ_API_KEY` | Live Smart Paste | Structured LLM extraction |
| `GOOGLE_MAPS_API_KEY` | Live Routes | Journey estimates, destination driving time, road access and carpark walking confirmation |
| `DATA_GOV_SG_API_KEY` | Official HDB availability | Optional server-side data.gov.sg authentication |
| `HDB_CARPARK_AVAILABILITY_*` | HDB availability | Cache, stale threshold and historical minimum-sample policy |
| `ONEMAP_EMAIL` / `ONEMAP_PASSWORD` | Live geocoding | Address standardisation |
| `LTA_ACCOUNT_KEY` | Full PT network | Bus stop/route snapshots |
| `CORS_ORIGINS` | No | Comma-separated web origins; local default is `http://localhost:3000,http://127.0.0.1:3000`. In production use only exact HTTPS frontend origins. |
| `NEXT_PUBLIC_API_BASE_URL` | Production web | Public HTTPS API URL baked into the Next.js build. |
| `NEXT_PUBLIC_DEPLOYMENT_ENV` | Production web | Set to `production`; this prevents a deployed web build from falling back to localhost. |

---

## Typical workflow

1. **Create session** on home page
2. **Set buyer profile** — budget, priorities, optional important location, schools toggle and up to 10 named schools
3. **Add 2–5 listings** — manual entry or Smart Paste; use **Remove** on a shortlist card to delete a flat you no longer want to compare
4. **Run enrichment** — geocode, fair price, transport, driving, schools, journeys
5. **Review comparison** — price/fair-price always expanded; recommendation at top
6. **Delete session** when done (`DELETE /api/v1/sessions/{id}`) for privacy

Removing a flat uses `DELETE /api/v1/sessions/{session_id}/listings/{listing_id}`.
The stable listing ID is used, listing-specific confirmed/input/enrichment data is
removed, and buyer-profile data plus other shortlist entries are preserved. The
workspace optimistically updates and restores the flat if the server rejects the
deletion.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Fair price shows “awaiting enrichment” | Save the profile and listings, then click **Run enrichment**; the button waits for the current queued run and reports provider failures |
| A listing cannot be added | NearHome rejects the same normalized address and asking price with a clear duplicate warning; change the listing details or remove the existing flat first |
| Journey estimates missing | Confirm important location with Places search + save profile |
| PT scores all similar | Expand `lta_bus_stops.json` via LTA ingest for your towns |
| Smart Paste 429 | Rate limit — wait 60s or adjust middleware |
| Integration tests skip | Start Postgres and run `alembic upgrade head` |
| `alembic check` reports many nullable/default differences | Review the existing ORM/database schema drift as a migration task; do not generate and apply an autogenerated migration without a schema review |
| `mypy app` fails | The backend has existing strict-typing debt; use `ruff` and the full pytest suite as the current enforced backend checks while the typing backlog is resolved |

For architecture details see `docs/architecture.md` and `docs/nearhome-spec.md`.
