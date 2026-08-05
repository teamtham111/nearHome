# Production deployment

NearHome's recommended deployment is **Vercel for the Next.js web app** and a
single **Render Blueprint for the FastAPI API, ARQ worker, PostgreSQL, and
Redis-compatible Key Value**. This split keeps browser-delivered code on a
Next.js-native platform while the API and worker run the Docker image that
contains Playwright Chromium and the required Linux libraries.

No production URL is committed to this repository. Replace the placeholders
below only in provider dashboards, never in source files.

## Architecture

```text
Browser -> https://<web-domain> (Vercel / Next.js)
              | HTTPS, NEXT_PUBLIC_API_BASE_URL
              v
          https://<api-domain> (Render / FastAPI + Playwright Chromium)
              | private network
              +--> Render PostgreSQL
              +--> Render Key Value / Redis -> Render ARQ worker
              +--> Google Routes, OneMap, Groq and optional LTA/data.gov.sg APIs
```

The API image packages the curated `data_pipeline/fixtures` reference data.
Smart Paste performs a bounded HTTP retrieval first and uses Playwright
Chromium only for an approved fallback; no local browser path is required.

## Deployment-readiness audit

| Finding | Classification | Resolution or rationale |
| --- | --- | --- |
| Client API URL fell back to `http://localhost:8000` | Must fix | Production builds now require `NEXT_PUBLIC_API_BASE_URL` and HTTPS when `NEXT_PUBLIC_DEPLOYMENT_ENV=production`. |
| API CORS used wildcard methods/headers and cross-origin credentials | Must fix | CORS now allows only configured origins, the methods NearHome uses, and `Content-Type`/`X-Request-ID`; no browser cookies are used. |
| API Docker image omitted runtime fixtures | Must fix | The Dockerfile now packages `data_pipeline/fixtures`, so enrichment evidence is available outside Compose bind mounts. |
| API container hard-coded port 8000 | Must fix | `scripts/start-api.sh` honours the host-provided `PORT`. |
| Readiness checked only PostgreSQL | Should fix | `/ready` now distinguishes database-unavailable from Redis-degraded with short timeouts and no connection details. |
| Redis exception messages could reveal an authenticated connection URL | Should fix | Queue and route-cache logs now record only safe category/type fields. |
| Localhost URLs in `.env.example`, Compose, CI, tests, and local setup docs | Safe/intentional | They are development/test defaults and are blocked from being used by the production web configuration. |
| Local absolute paths in setup material | Safe/intentional | They occur in explicitly local-development guidance, not runtime configuration. |
| `http://` URLs in public data-source fixtures and provider documentation | Safe/intentional | These are third-party data/feed references; browser-to-API production traffic is constrained to HTTPS. |

## Deploy the backend and worker

1. Put this repository in a Git provider repository. The workspace currently
   has no `.git` metadata, so this must be done before provider deployment.
2. In Render, choose **New > Blueprint**, connect the repository, and select
   [`render.yaml`](../render.yaml). Choose the same region for every service
   (the blueprint uses Singapore).
3. When prompted for `sync: false` values for `nearhome-api`, set these server
   variables. Use the API's public URL only after Render has created it:

   | Variable | Required | Value |
   | --- | --- | --- |
   | `WEB_URL` | Yes | `https://<vercel-web-domain>` |
   | `CORS_ORIGINS` | Yes | Exactly `https://<vercel-web-domain>`; comma-separate only known additional domains |
   | `GOOGLE_MAPS_API_KEY` | Yes | Server-restricted key with Routes/Places access as required |
   | `ONEMAP_EMAIL`, `ONEMAP_PASSWORD` | Yes | OneMap account credentials |
   | `GROQ_API_KEY` | Yes | Groq server API key |
   | `DATA_GOV_SG_API_KEY`, `LTA_ACCOUNT_KEY` | Optional | Provider credentials when live sources need them |

   Render creates `SECRET_KEY`, wires `DATABASE_URL` and `REDIS_URL` over its
   private network, and leaves those connection strings out of the source
   repository. Copy the provider variables to `nearhome-worker` too; they are
   deliberately not shared by an environment group because those credentials
   must be entered in the Render dashboard.
4. Verify that `nearhome-api` deploys only after `alembic upgrade head` runs.
   The pre-deploy command is configured in the blueprint and requires the
   selected paid API plan. Never replace it with a destructive schema reset.
5. Record the actual API URL as `https://<render-api-domain>`. Confirm:

   ```bash
   curl --fail https://<render-api-domain>/api/v1/health
   curl --fail https://<render-api-domain>/api/v1/ready
   ```

`/health` proves the process is running. `/ready` reports `ready`, `degraded`
(Postgres works but Redis is unavailable), or `unavailable` (Postgres is not
usable), without disclosing connection strings.

## Deploy the frontend

1. Import the same repository into Vercel and set the **Root Directory** to
   `apps/web`. Vercel detects the Next.js application; use Node.js 22.
2. Set these build-time environment variables for **Production**:

   | Variable | Secret | Value |
   | --- | --- | --- |
   | `NEXT_PUBLIC_DEPLOYMENT_ENV` | No | `production` |
   | `NEXT_PUBLIC_API_BASE_URL` | No | `https://<render-api-domain>` |
   | `NEXT_PUBLIC_DEMO_MODE` | No | `false` |

   Do **not** put Google, Groq, database, Redis, or session secrets in Vercel
   environment variables. `NEXT_PUBLIC_*` values are bundled for the browser.
   The build fails clearly if a production deployment omits the API base URL or
   uses a non-HTTPS API URL.
4. NearHome's existing same-origin `/api/geocode` route makes OneMap requests
   server-side on Vercel. Add `ONEMAP_EMAIL` and `ONEMAP_PASSWORD` as **server
   environment variables** (without `NEXT_PUBLIC_`) to Vercel as well. They are
   required for live important-location and named-school address search, remain
   in Vercel's server bundle only, and must never be exposed to browser code.
5. Deploy, add a custom domain if desired, then set that final HTTPS origin as
   both `WEB_URL` and `CORS_ORIGINS` in Render and redeploy the API.
6. The frontend currently has no configured public GitHub repository URL, so a
   footer repository link cannot be safely added until the real repository URL
   is available. Add it as an ordinary public link after the repository exists.

## Security and operations

- Store secrets only in Render's secret environment fields. Rotate a provider
  key immediately if it has ever been committed; removing it from a current
  file does not remove Git history.
- Keep Google API restrictions compatible with server-to-server calls. Browser
  referrer-only restrictions will reject the API's Google Routes requests.
- Restrict `CORS_ORIGINS` to exact HTTPS browser origins. The API neither uses
  wildcard origins nor permits cross-origin credentials.
- Render's internal Postgres and Key Value URLs are used between services. For
  an external PostgreSQL provider, require TLS in its supplied `DATABASE_URL`.
- Back up the managed database according to the selected Render plan. Do not
  use a seed/reset command against production.
- API logs include method, path, status, duration, request ID, and safe error
  category. They intentionally exclude headers, API keys, provider payloads,
  scraped page content, and connection strings.

## Production environment-variable reference

| Variable | Runtime | Secret | Required | Configuration location |
| --- | --- | --- | --- | --- |
| `NEXT_PUBLIC_DEPLOYMENT_ENV` | Web | No | Yes | Vercel Production (`production`) |
| `NEXT_PUBLIC_API_BASE_URL` | Web | No | Yes | Vercel Production (public HTTPS Render API URL) |
| `NEXT_PUBLIC_DEMO_MODE` | Web | No | Yes | Vercel Production (`false`) |
| `ONEMAP_EMAIL`, `ONEMAP_PASSWORD` | Web server route | Yes | Yes | Vercel server environment; never use `NEXT_PUBLIC_` |
| `APP_ENV` | API and worker | No | Yes | Render Blueprint (`production`) |
| `DEMO_MODE` | API and worker | No | Yes | Render Blueprint (`false`) |
| `LOG_LEVEL` | API and worker | No | Yes | Render Blueprint (`INFO`) |
| `WEB_URL`, `CORS_ORIGINS` | API | No | Yes | Render API secret/environment fields, set to exact Vercel origin |
| `SECRET_KEY` | API and worker | Yes | Yes | Generated/wired by Render Blueprint |
| `DATABASE_URL`, `REDIS_URL` | API and worker | Yes | Yes | Private Render references in the Blueprint |
| `GOOGLE_MAPS_API_KEY` | API and worker | Yes | Yes | Render secret fields |
| `ONEMAP_EMAIL`, `ONEMAP_PASSWORD` | API and worker | Yes | Yes | Render secret fields |
| `GROQ_API_KEY` | API and worker | Yes | Yes | Render secret fields |
| `DATA_GOV_SG_API_KEY`, `LTA_ACCOUNT_KEY` | API and worker | Yes | Optional | Render secret fields when live data sources require them |

`HDB_CARPARK_*`, provider model/version settings, and rate-limit settings retain
their safe defaults from `.env.example` unless a conscious operational change
is needed. Do not copy a local `.env` file to either hosting provider.

## Validate production

After both public URLs exist, run only the read-only checks:

```bash
FRONTEND_URL=https://<vercel-web-domain> \
BACKEND_URL=https://<render-api-domain> \
./scripts/production-smoke.sh
```

Then use an incognito window or another device to verify manually:

1. Load the web app and directly reload a non-root application route.
2. Create a buyer profile, add two manual listings, edit and remove one, and
   confirm comparison and recommendation rendering.
3. Smart Paste one supported listing URL and one copied listing text; confirm
   review/edit before confirmation and a useful fallback if the listing site
   blocks retrieval.
4. Run enrichment for multiple listings. Confirm remaining lease, fair-price,
   public transport, driving, important locations, and schools each show their
   real provider/reference-data provenance or a clear provider failure.
5. In browser developer tools, confirm requests target the HTTPS API domain,
   not `localhost`, and that no secret is in HTML, bundles, or API responses.
6. Temporarily test an unavailable optional provider only if it can be done
   without changing production credentials; its error must be understandable
   and must include the request ID where applicable.

## Common deployment failures

| Symptom | Cause and fix |
| --- | --- |
| Web build says `NEXT_PUBLIC_API_BASE_URL is required` | Add the HTTPS API URL and `NEXT_PUBLIC_DEPLOYMENT_ENV=production` in Vercel, then rebuild. |
| Browser CORS request fails | Make `WEB_URL` and `CORS_ORIGINS` exactly match the deployed Vercel origin, including `https://`, then redeploy the API. |
| API fails startup with production configuration error | Supply the named non-secret/secret variable; the startup error never prints its value. |
| `/ready` is degraded | Postgres is healthy but Redis/worker queue is unavailable. Inspect Render Key Value and worker logs; inline enrichment remains available. |
| Smart Paste browser fallback fails | Use a Render Docker service with this repository Dockerfile. It installs Playwright Chromium and system dependencies; do not configure a local browser executable path. |
| Google routing fails in production | Enable billing and the Routes API for the same Google Cloud project as the key, and use server-compatible API-key restrictions. |
