# Production deployment: Vercel, Cloud Run, and Supabase

NearHome deploys its Next.js web application to **Vercel** and its existing
FastAPI service to **Google Cloud Run**. Supabase is used only as managed
PostgreSQL; NearHome continues to use SQLAlchemy, Alembic, and its existing
repository/service architecture.

```text
Browser -> Vercel / Next.js -> Cloud Run / FastAPI -> Supabase PostgreSQL
                                  |
                                  +-> Google Routes/Places, OneMap, Groq, LTA/data.gov.sg
                                  +-> Playwright Chromium fallback (only after HTTP retrieval fails)
```

The first production deployment uses `JOB_EXECUTION_MODE=inline`: an
enrichment request executes in the Cloud Run API instance, persists existing
progress/results in PostgreSQL, and returns the same `inline` response shape
the frontend already supports. Redis and an ARQ worker are not required for
this deployment. Cloud Run is deliberately limited to one instance and one
concurrent request, so the in-process enrichment semaphore is only a
per-instance guard, not a distributed lock.

## Before deployment

1. Create a Supabase project in the desired region.
2. In Supabase **Connect**, copy the **Session Pooler** connection string for
   application traffic when direct IPv6 connectivity is unavailable. NearHome
   accepts either `postgresql://` or `postgresql+psycopg://` and normalizes the
   former to the installed psycopg v3 driver. Production connections are
   explicitly opened with `sslmode=require`.
3. Keep the direct connection string available only if needed for maintenance;
   use a single, appropriate connection string in the `DATABASE_URL` secret.
4. Create a Google Cloud project and a dedicated Cloud Run runtime service
   account. Do not download a service-account JSON key.
5. Install/authenticate the Google Cloud CLI and select the project:

   ```bash
   gcloud auth login
   gcloud config set project <google-cloud-project-id>
   ```

Supabase documents Session Pooler as the IPv4-compatible alternative for
persistent application clients, while direct endpoints are appropriate for
maintenance when reachable. [Supabase connection guidance](https://supabase.com/docs/guides/database/connecting-to-postgres)

## Google Secret Manager

Create each required secret without putting a value in shell history or source
control. This command reads the value from standard input:

```bash
printf '%s' '<value-entered-interactively>' | \
  gcloud secrets create nearhome-database-url --data-file=-
```

Create these secret names (or override their names through the deployment
script environment variables):

| Secret Manager secret | NearHome variable | Required |
| --- | --- | --- |
| `nearhome-database-url` | `DATABASE_URL` | Yes |
| `nearhome-secret-key` | `SECRET_KEY` | Yes |
| `nearhome-google-maps-api-key` | `GOOGLE_MAPS_API_KEY` | Yes for live routes/places |
| `nearhome-onemap-email` | `ONEMAP_EMAIL` | Yes for live geocoding |
| `nearhome-onemap-password` | `ONEMAP_PASSWORD` | Yes for live geocoding |
| `nearhome-groq-api-key` | `GROQ_API_KEY` | Yes for live Smart Paste |

Optional `LTA_ACCOUNT_KEY` and `DATA_GOV_SG_API_KEY` can be added later only
if their live-source rate limits are needed. The data.gov.sg availability feed
works without inventing an API key.

Cloud Run's service identity needs `roles/secretmanager.secretAccessor` for
each attached secret. The deployment script grants that role by secret name;
it never reads or prints secret values. [Cloud Run secret configuration](https://cloud.google.com/run/docs/configuring/services/secrets)

## Migrate Supabase once

Activate the API environment and run migrations once before deploying a new
revision. Do not run migrations in every Cloud Run startup and never reset a
production database.

```bash
cd apps/api
source .venv/bin/activate
cd ../..
GOOGLE_CLOUD_PROJECT=<google-cloud-project-id> \
DATABASE_URL_SECRET_NAME=nearhome-database-url \
./scripts/migrate-supabase.sh
```

The script retrieves `DATABASE_URL` only into its process environment and runs
`alembic upgrade head`. Existing migration history is unchanged.

## Deploy the API to Cloud Run

Make the scripts executable once:

```bash
chmod +x scripts/deploy-cloud-run.sh scripts/migrate-supabase.sh
```

Deploy with the exact final Vercel origin. The script builds with Cloud Build,
pushes to Artifact Registry, attaches Secret Manager values, and deploys the
public API in `asia-southeast1` by default:

```bash
GOOGLE_CLOUD_PROJECT=<google-cloud-project-id> \
CLOUD_RUN_SERVICE_ACCOUNT=<runtime-service-account>@<google-cloud-project-id>.iam.gserviceaccount.com \
WEB_URL=https://<your-project>.vercel.app \
CORS_ORIGINS=https://<your-project>.vercel.app \
./scripts/deploy-cloud-run.sh <google-cloud-project-id>
```

The service uses request-based CPU allocation, `min-instances=0`,
`max-instances=1`, `concurrency=1`, `1` CPU, `2Gi` memory, and a 600-second
request timeout. Cloud Run injects `PORT`; the container listens on
`0.0.0.0:${PORT:-8080}` with one Uvicorn worker, as required by the [Cloud Run
container contract](https://cloud.google.com/run/docs/container-contract).

The API image includes Python 3.12, Playwright Chromium and Linux dependencies,
the CatBoost inference code, and `data_pipeline/fixtures`. CatBoost fitting is
lazy and cached once per container from the immutable transaction snapshot;
Chromium is never launched during startup or health checks.

## Configure Vercel

Set these Vercel **Production** build variables after the first Cloud Run
deploy prints its HTTPS URL:

| Variable | Value | Public |
| --- | --- | --- |
| `NEXT_PUBLIC_DEPLOYMENT_ENV` | `production` | Yes |
| `NEXT_PUBLIC_API_BASE_URL` | `https://<cloud-run-service-url>` | Yes |
| `NEXT_PUBLIC_DEMO_MODE` | `false` | Yes |

Do not add any backend secret as `NEXT_PUBLIC_*`. The existing Next.js
server-side `/api/geocode` route does require `ONEMAP_EMAIL` and
`ONEMAP_PASSWORD` as ordinary Vercel server environment variables; they are
not client variables and must match the provider credentials used by the API.

After Vercel has a final domain, update Cloud Run's `WEB_URL` and
`CORS_ORIGINS` to that exact HTTPS origin and redeploy. Add preview domains to
`CORS_ORIGINS` only when they are known and required; CORS controls browser
origins, not authentication.

## Health checks and smoke test

Cloud Run health checks use existing lightweight endpoints:

```bash
curl --fail https://<cloud-run-service-url>/api/v1/health
curl --fail https://<cloud-run-service-url>/api/v1/ready
```

`/api/v1/health` only confirms the process is alive. `/api/v1/ready` performs a bounded
database check; it does not load CatBoost, launch Chromium, or call external
providers. In inline mode Redis reports `not_required`.

After Vercel is configured, use the read-only smoke check:

```bash
FRONTEND_URL=https://<your-project>.vercel.app \
BACKEND_URL=https://<cloud-run-service-url> \
./scripts/production-smoke.sh
```

Then test in an incognito window: create/load a session, add manual and Smart
Paste listings, confirm/edit fields, run enrichment, inspect transport,
driving, journeys, schools, fair price, and recommendation evidence, and
remove a listing. Verify browser requests target Cloud Run—not localhost—and
that browser bundles contain no secrets.

## Environment reference

### Cloud Run normal variables

`APP_ENV=production`, `DEMO_MODE=false`, `LOG_LEVEL=INFO`, `WEB_URL`,
`CORS_ORIGINS`, `JOB_EXECUTION_MODE=inline`, `MAX_CONCURRENT_ENRICHMENTS=1`,
`ENABLE_PLAYWRIGHT_FALLBACK=true`, `PLAYWRIGHT_TIMEOUT_SECONDS=25`,
`PLAYWRIGHT_MAX_CONCURRENCY=1`, `DATABASE_POOL_SIZE=3`,
`DATABASE_MAX_OVERFLOW=2`, and `DATABASE_POOL_RECYCLE_SECONDS=300`.

### Google Secret Manager values

`DATABASE_URL`, `SECRET_KEY`, `GOOGLE_MAPS_API_KEY`, `ONEMAP_EMAIL`,
`ONEMAP_PASSWORD`, and `GROQ_API_KEY`; optional `LTA_ACCOUNT_KEY` and
`DATA_GOV_SG_API_KEY` only when configured. `REDIS_URL` is omitted in inline
mode.

### Vercel values

`NEXT_PUBLIC_DEPLOYMENT_ENV`, `NEXT_PUBLIC_API_BASE_URL`, and
`NEXT_PUBLIC_DEMO_MODE` are public build-time values. `ONEMAP_EMAIL` and
`ONEMAP_PASSWORD` are Vercel server-only variables for `/api/geocode`.

### Local-only defaults

Use `.env` copied from `.env.example`. Local PostgreSQL is supported. Inline
mode works with no Redis. For the optional local ARQ worker, set
`JOB_EXECUTION_MODE=arq` and a `REDIS_URL`, then start
`python -m app.jobs.worker`.

## Operational limitations and later scaling

- Scale-to-zero may make the first request slower; the frontend reports a
  friendly service-starting message for production network failures.
- One Cloud Run instance/concurrent request means one heavy enrichment at a
  time. The API returns an existing inline run rather than starting a duplicate
  for the same session in the same instance.
- Browser fallback is best effort. HTTP retrieval remains first, SSRF checks
  remain enabled, and users receive a copy/paste fallback if both methods fail.
- In `DEMO_MODE`, Smart Paste uses deterministic fixture extraction that
  recognises common block-prefixed and bare-block Singapore address text; live
  deployments continue to use Groq extraction.
- To restore queue-backed execution later, provision Redis, set
  `JOB_EXECUTION_MODE=arq`, and run the existing worker. The enrichment
  business logic remains unchanged.
