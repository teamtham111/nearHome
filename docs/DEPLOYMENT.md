# Production deployment: Vercel, Cloud Run, and Supabase

NearHome deploys its Next.js web application to **Vercel** and its existing
FastAPI service to **Google Cloud Run**. Supabase is used only as managed
PostgreSQL; NearHome continues to use SQLAlchemy, Alembic, and its existing
repository/service architecture.

```text
Browser -> Vercel / Next.js -> public Cloud Run API -> Cloud Tasks -> private Cloud Run worker
                                  |                       |                    |
                                  +-----------------------+---- Supabase PostgreSQL
                                                                      |
                                                                      +-> Google Routes/Places, OneMap, Groq, LTA/data.gov.sg
                                                                      +-> Playwright Chromium fallback (only after HTTP retrieval fails)
```

Production uses `JOB_EXECUTION_MODE=cloud_tasks`. The public API creates an
`enrichment_jobs` row, enqueues only its UUID, and returns HTTP 202. Cloud Tasks
uses an OIDC token to invoke a private worker, which claims the job atomically,
persists progress/results in PostgreSQL, and is safe to invoke more than once.
Redis/ARQ is retained only as local legacy compatibility; it is not required in
production. Inline mode is local/test-only and production startup rejects it.

## Configure the durable enrichment queue and worker

Run the migration before any service reads job state:

```bash
cd apps/api && source .venv/bin/activate && alembic upgrade head
```

Create a dedicated service account for Cloud Tasks OIDC delivery and configure
the bounded queue. The public API runtime identity receives only
`roles/cloudtasks.enqueuer` and scoped `roles/iam.serviceAccountUser` on the
task-delivery identity, allowing it to create an OIDC task as that identity.
The task-delivery identity receives only `roles/run.invoker` on the private
worker. Cloud Tasks' service agent needs
`roles/iam.serviceAccountTokenCreator` on that delivery identity so it can mint
the OIDC token. The helper script applies these bindings without reading any
secret values:

```bash
GOOGLE_CLOUD_PROJECT=<project-id> \
CLOUD_RUN_SERVICE_ACCOUNT=<public-api-runtime>@<project-id>.iam.gserviceaccount.com \
CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL=<task-invoker>@<project-id>.iam.gserviceaccount.com \
./scripts/configure-enrichment-queue.sh <project-id>
```

Deploy the worker privately first. Unless `ENRICHMENT_WORKER_IMAGE` is provided,
its script builds the shared API/worker image itself. It uses concurrency one and
one maximum instance by default because Playwright may run there:

```bash
GOOGLE_CLOUD_PROJECT=<project-id> \
ENRICHMENT_WORKER_SERVICE_ACCOUNT=<worker-runtime>@<project-id>.iam.gserviceaccount.com \
CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL=<task-invoker>@<project-id>.iam.gserviceaccount.com \
WEB_URL=https://<your-project>.vercel.app \
CORS_ORIGINS=https://<your-project>.vercel.app \
./scripts/deploy-enrichment-worker.sh <project-id>
```

Copy the emitted private worker URL into `ENRICHMENT_WORKER_URL`, then deploy
the public API with `CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL` and that URL set. The
worker must remain `--no-allow-unauthenticated`; Cloud Run IAM plus the Cloud
Tasks OIDC token rejects public calls. Do not add service-account JSON keys.

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

   Keep downloaded CLI installers and SDK directories outside this repository;
   they are local tooling, never application build inputs.

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

Use `printf '%s'`, never `echo`, for connection strings and other exact secret
values: `echo` appends a newline, and Cloud Run preserves that byte in an
environment variable. For example, a newline after a PostgreSQL URL becomes
part of the database name and makes the readiness check fail. To correct an
existing version without printing the value, create a newline-free replacement:

```bash
gcloud secrets versions access latest --secret=nearhome-database-url | \
  tr -d '\n' | \
  gcloud secrets versions add nearhome-database-url --data-file=-
```

Create a new Cloud Run revision afterwards so new instances use that version.

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

Deploy with the exact final Vercel origin. The script builds with Cloud Build
using the root [`cloudbuild.yaml`](../cloudbuild.yaml) definition, which
selects `apps/api/Dockerfile` while retaining the repository-root build
context for reference fixtures. It pushes to Artifact Registry, attaches
Secret Manager values, and deploys the public API in `asia-southeast1` by
default:

```bash
GOOGLE_CLOUD_PROJECT=<google-cloud-project-id> \
CLOUD_RUN_SERVICE_ACCOUNT=<runtime-service-account>@<google-cloud-project-id>.iam.gserviceaccount.com \
CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL=<task-invoker>@<google-cloud-project-id>.iam.gserviceaccount.com \
ENRICHMENT_WORKER_URL=https://<private-worker-url> \
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
the CatBoost inference code, its prebuilt image-local artifact, and
`data_pipeline/fixtures`. The artifact is trained during image build from the
immutable transaction snapshot; runtime only loads and validates it, never fits
or calibrates during enrichment. Chromium is never launched during startup or
health checks.

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

Run Vercel from the repository root, which is linked to the project whose Root
Directory is `apps/web`. The root [`.vercelignore`](../.vercelignore) is an
allow-list for `apps/web` only: it prevents root `.env` files, the Cloud Run
API, local tooling, and test/data directories from entering a frontend upload.
The web build command also deliberately skips the root `.env` when Vercel sets
`VERCEL=1`, so the Vercel Production variables remain authoritative. Validate
the source manifest before a manual deployment with `vercel deploy --dry`.

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
providers. In Cloud Tasks mode Redis reports `not_required` and `job_execution`
reports `cloud_tasks`. Both responses include a non-secret `git_sha` supplied as
`GIT_SHA` when Cloud Build creates the image. Record this value and compare it
with the commit intended for the deployment. A locally built image may report
`"unknown"`; that does not change readiness semantics.

The worker logs the same `git_sha` at startup and for each enrichment state
transition. Confirm the worker revision with Cloud Run logs, for example:

```bash
gcloud run services logs read <worker-service-name> \
  --region asia-southeast1 --limit 50 | grep enrichment_worker_started
```

Production startup fails if `GIT_SHA` is missing, `unknown`, or not a full
40-character commit SHA. This prevents a healthy-looking production revision
from claiming release provenance it does not have.

Build the API first with `scripts/deploy-cloud-run.sh`; it builds exactly one
immutable image tagged with the current commit SHA. Then deploy the worker with
`scripts/deploy-enrichment-worker.sh`. The worker script refuses to rebuild: it
resolves the already-built API image by SHA tag, so Cloud Run must use the same
image digest for both services. Set `RELEASE_IMAGE_NAME` only when a project
uses a non-default Artifact Registry image name, and use the same value for
both scripts. Both scripts resolve the tag to `image@sha256:...` before
deploying, so Cloud Run revisions reference immutable content. Verify this
after deployment:

```bash
gcloud run services describe nearhome-api --region asia-southeast1 \
  --format='value(spec.template.spec.containers[0].image)'
gcloud run services describe nearhome-enrichment-worker --region asia-southeast1 \
  --format='value(spec.template.spec.containers[0].image)'
```

After Vercel is configured, use the read-only smoke check:

```bash
FRONTEND_URL=https://<your-project>.vercel.app \
BACKEND_URL=https://<cloud-run-service-url> \
./scripts/production-smoke.sh
```

Then use an incognito window for this controlled-beta checklist:

1. Create a fresh session, save a buyer profile, and reload the page to verify
   the session persists.
2. Smart Paste one PropertyGuru listing and one 99.co listing; review and
   confirm the extracted fields before adding each flat.
3. Add one manual listing in square metres and one in square feet. Confirm that
   the latter is shown/saved in the equivalent square metres.
4. Start enrichment and confirm the progress display advances through stages
   to a terminal `completed` or useful `failed` state; it must not poll forever.
5. Load the comparison and inspect fair-price, public-transport, driving,
   journeys, schools, and recommendation evidence. Reload the session and
   confirm those results persist.
6. Repeat the core flow at phone width. Deliberately submit an invalid listing
   URL and an unresolvable address; verify that the errors are recoverable and
   useful.
7. In browser Network tools, investigate unexpected 400, 404, 422, 429, 500,
   502, or 503 responses. Verify requests target Cloud Run—not localhost—and
   that browser bundles contain no secrets.
8. Record `/api/v1/ready`'s `git_sha` and compare it with the API deployment
   commit. Confirm the same SHA in the worker startup log before treating the
   release as a matched API/worker pair.

## Environment reference

### Cloud Run normal variables

`APP_ENV=production`, `DEMO_MODE=false`, `LOG_LEVEL=INFO`, `WEB_URL`,
`CORS_ORIGINS`, `JOB_EXECUTION_MODE=cloud_tasks`, `GCP_PROJECT_ID`,
`CLOUD_TASKS_LOCATION`, `CLOUD_TASKS_QUEUE`, `ENRICHMENT_WORKER_URL`,
`CLOUD_TASKS_SERVICE_ACCOUNT_EMAIL`, optional `CLOUD_TASKS_OIDC_AUDIENCE`,
`CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS=600`, `MAX_ENRICHMENT_JOB_ATTEMPTS=3`,
`ENRICHMENT_JOB_STALE_SECONDS=660`,
`MAX_CONCURRENT_ENRICHMENTS=1`,
`ENABLE_PLAYWRIGHT_FALLBACK=true`, `PLAYWRIGHT_TIMEOUT_SECONDS=25`,
`PLAYWRIGHT_MAX_CONCURRENCY=1`, `DATABASE_POOL_SIZE=3`,
`DATABASE_MAX_OVERFLOW=2`, and `DATABASE_POOL_RECYCLE_SECONDS=300`.

### Google Secret Manager values

`DATABASE_URL`, `SECRET_KEY`, `GOOGLE_MAPS_API_KEY`, `ONEMAP_EMAIL`,
`ONEMAP_PASSWORD`, and `GROQ_API_KEY`; optional `LTA_ACCOUNT_KEY` and
`DATA_GOV_SG_API_KEY` only when configured. `REDIS_URL` is omitted in Cloud
Tasks mode.

### Vercel values

`NEXT_PUBLIC_DEPLOYMENT_ENV`, `NEXT_PUBLIC_API_BASE_URL`, and
`NEXT_PUBLIC_DEMO_MODE` are public build-time values. `ONEMAP_EMAIL` and
`ONEMAP_PASSWORD` are Vercel server-only variables for `/api/geocode`.

### Local-only defaults

Use `.env` copied from `.env.example`. Local PostgreSQL is supported. Inline
mode is allowed only locally and completes synchronously for simple development.
To exercise the real queue path locally, point Cloud Tasks at a reachable worker
URL with development configuration. ARQ remains a legacy local option, not the
production enrichment architecture.

## Operational limitations and later scaling

- Scale-to-zero may make the first request slower; the frontend reports a
  friendly service-starting message for production network failures.
- The public API never performs provider enrichment inline in production. It
  returns HTTP 202 with a job status URL; the browser polls this lightweight
  database-backed endpoint with bounded backoff.
- The queue is configured with conservative dispatch rate/concurrency of one.
  Cloud Tasks may deliver a task more than once, so the worker atomically claims
  queued jobs and returns success for already-running/completed jobs.
- Browser fallback is best effort. HTTP retrieval remains first, SSRF checks
  remain enabled, and users receive a copy/paste fallback if both methods fail.
- In `DEMO_MODE`, Smart Paste uses deterministic fixture extraction that
  recognises common block-prefixed and bare-block Singapore address text; live
  deployments continue to use Groq extraction.
- Roll back safely by first pausing the Cloud Tasks queue, then redeploying the
  prior API revision. Do not switch a production revision to inline mode: the
  configuration validator rejects it. Existing completed job/result rows remain
  in PostgreSQL for comparison display and audit.
