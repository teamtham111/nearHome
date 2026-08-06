# Enrichment performance operations

NearHome keeps the production flow unchanged:

```text
Vercel -> public Cloud Run API -> Cloud Tasks -> private Cloud Run worker -> Supabase PostgreSQL
```

The worker logs `enrichment_stage_timing` and one
`enrichment_performance_summary` per job. Logs contain job/session IDs and
aggregate counts only; they do not include API keys, provider headers, raw
addresses, or complete provider payloads.

## Confirmed bottlenecks addressed

- The previous worker created and closed an `httpx.Client` for every Google
  Routes request. A provider instance now reuses one pooled client per job.
- Static HDB carpark records were mirrored during every driving enrichment run.
  Import is now an explicit maintenance command and reads all existing rows in
  one query rather than one lookup per carpark.
- Enriched fields and route/run state previously committed individually.
  Related writes are coalesced, while a `RUNNING` status is committed before a
  slow provider call so an external call never waits with unsaved work.
- CatBoost training and interval calibration used to occur on a cold worker's
  first fair-price request. Runtime now loads only a prebuilt artifact.
- Independent candidate routes use a deterministic bounded thread pool. The
  default is four calls; whole listings are deliberately not parallelised.

## HDB carpark import

Run after refreshing the official fixture, before deploying an image that uses
the refreshed data. It is safe to repeat: an unchanged source performs no
write, while changed rows are updated in one commit.

```bash
cd /path/to/nearhomev2
python data_pipeline/ingest_hdb_carparks.py --live --persist-db
```

An empty source is reported as `empty_source` and never clears existing HDB
carpark evidence. Normal enrichment does not run this command automatically.

## CatBoost artifact

Build a matching local artifact whenever the transaction fixture changes:

```bash
./scripts/train-fair-price-model.sh
```

For local development, set
`FAIR_PRICE_MODEL_ARTIFACT_PATH=artifacts/fair_price/catboost` in the root
`.env`. Relative paths are resolved from the repository root, so API and ARQ
worker processes use the same artifact regardless of their working directory.
Restart both processes after rebuilding. Cloud Run builds create
`/app/model-artifacts/fair-price` inside the image automatically. Runtime
validates the model version, feature columns and transaction snapshot before
using it. If validation fails, fair price keeps its existing transparent
weighted-comparables fallback rather than retraining.

## Optional production Redis route cache

Redis is optional. Without `REDIS_URL`, enrichment remains correct and simply
does not share route responses between worker instances. With Redis configured,
route cache keys are deterministic and include a versioned namespace,
coordinates rounded to four decimals, mode, time bucket and route preferences.

```dotenv
REDIS_URL=rediss://<username>:<password>@<host>:<port>/0
ROUTE_CACHE_NAMESPACE=nearhome:routes:v1
ROUTE_REQUEST_CONCURRENCY=4
```

Never put this URL in browser variables or logs. A managed Redis provider or
Google Memorystore are both possible production options; neither is required
by NearHome and neither is provisioned by this repository. Walking routes use a
24-hour TTL, transit structure six hours, and traffic-aware driving 15 minutes.
Only successful results are cached.

## Benchmarking

Use the local benchmark harness only against `localhost` after bringing up the
API, worker, local PostgreSQL and fixtures. It refuses non-local targets. Create
the listed representative local sessions first, then run:

```bash
python scripts/benchmark-enrichment.py \
  --scenario pt-only=<session-id> \
  --scenario driving-only=<session-id> \
  --scenario both=<session-id> \
  --scenario three-listing-both=<session-id> \
  --scenario cold=<session-id> \
  --scenario warm=<session-id> \
  --scenario cache-repeat=<session-id>
```

It writes only aggregate job/stage timing data to
`benchmark-enrichment-results.json`; compare this file with the worker's
`enrichment_performance_summary` for route/cache/commit metrics. Also run the
deterministic local suite:

```bash
cd apps/api
source .venv/bin/activate
pytest app/tests/test_routing_batch.py app/tests/test_fair_price_artifact.py \
  app/tests/test_public_transport_engine.py app/tests/test_driving_engine.py
```

For a full worker measurement, run an enrichment job against a local database
with `DEMO_MODE=true`, then collect its `enrichment_performance_summary` log.
Record cold/warm duration, stage timings, route count, cache hits/misses and
database commit count. Do not point this exercise at a production database.

## Cold-start review

The worker deployment currently uses 1 CPU, 2 GiB memory, `min-instances=0`,
`max-instances=1`, concurrency 1 and request-based CPU. The code changes remove
runtime model fitting and repeated carpark imports, so a minimum instance is no
longer the primary latency control. `min-instances=1` would reduce cold-start
latency but has an ongoing Cloud Run cost; measure cold/warm logs first before
choosing it. CPU boost should be evaluated with the same measurement rather
than enabled as a substitute for route/database optimisation.

## Rollback

The changes are reversible without a database migration: unset the optional
Redis variables, revert the application revision, and retain the current
carpark table. Do not restore per-request carpark import or runtime CatBoost
training; if an artifact is unavailable, the existing comparable fallback is
the safe behaviour.
