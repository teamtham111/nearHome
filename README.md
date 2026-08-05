# NearHome

Explainable decision-support application for Singapore HDB resale buyers who have already shortlisted approximately two to five actual listings.

NearHome is **not** a property search portal, grant calculator, mortgage adviser, or official HDB valuation service.

## Product purpose

1. Create a buyer decision profile (budget, up to three priorities, supported hard requirements and optional named schools).
2. Add listings through one shared **Add a flat** panel with Manual entry or LLM-first Smart Paste confirmation. Smart Paste accepts copied listing text or a PropertyGuru Singapore listing URL; URL pages are retrieved server-side before Groq extraction.
3. Remove any confirmed flat from the shortlist using its stable listing ID; listing-specific evidence is removed while the buyer profile and other flats remain.
4. See immediate factual comparison after two confirmed listings.
4. Progressively enrich with official and provider data.
5. Receive a deterministic, explainable recommendation.

## Stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 15, React 19, TypeScript, Tailwind, React Hook Form, Zod, TanStack Query |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic |
| Database | PostgreSQL |
| Cache / jobs | Redis, ARQ |
| Data processing | pandas/CatBoost for HDB transaction ingestion and fair-price estimation |
| Tests | pytest, Vitest, Playwright |

## Quick start

**Full local setup guide:** [docs/SETUP.md](docs/SETUP.md)  
**Production deployment guide (Vercel + Cloud Run + Supabase):** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

```bash
cp .env.example .env
chmod +x scripts/start-local.sh
./scripts/start-local.sh
```

Terminal 1 — API:

```bash
cd apps/api && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Terminal 2 — Web:

```bash
cd apps/web && npm install
# The Next server route uses OneMap server-side; load the root .env for local live mode.
set -a && source ../../.env && set +a
npm run dev
```

Open http://localhost:3000

### Docker

```bash
docker compose up --build
```

## Production architecture

```text
Vercel / Next.js -> Google Cloud Run / FastAPI -> Supabase PostgreSQL
```

Cloud Run uses inline enrichment by default, so its first deployment does not
need Redis or an ARQ worker. The API Docker image packages Playwright Chromium
and the reference fixtures required by Smart Paste and enrichment. Follow the
[production deployment guide](docs/DEPLOYMENT.md) for Secret Manager,
Supabase migrations, Cloud Run deployment, Vercel configuration, and smoke
tests.

## Environment variables

See `.env.example`. Provider secrets (`GOOGLE_MAPS_API_KEY`, `DATA_GOV_SG_API_KEY`, `GROQ_API_KEY`, etc.) are **server-side only**.

Set `DEMO_MODE=true` for local development without paid API keys.

For production, the browser receives only `NEXT_PUBLIC_API_BASE_URL`,
`NEXT_PUBLIC_DEPLOYMENT_ENV=production`, and other deliberately public web
configuration. The API requires `APP_ENV=production`, `DEMO_MODE=false`, exact
HTTPS `WEB_URL`/`CORS_ORIGINS`, a non-default `SECRET_KEY`, database access,
and Google/OneMap/Groq credentials. See the [deployment guide](docs/DEPLOYMENT.md)
for the full variable table, hosting architecture, migration command, health
checks, secret-management rules, Smart Paste Chromium requirement, and the
read-only production smoke test.

## Commands

| Task | Command |
| --- | --- |
| DB migrations | `cd apps/api && alembic upgrade head` |
| API tests | `cd apps/api && pytest` |
| API integration tests | `DATABASE_URL=... pytest app/tests/test_api_integration.py` |
| Worker | `cd apps/api && python -m app.jobs.worker` |
| HDB carpark data ingest | `python data_pipeline/ingest_hdb_carparks.py --live` |
| HDB transaction ingest | `python data_pipeline/ingest_hdb_transactions.py path/to.csv` |
| Web tests | `cd apps/web && npm test` |
| E2E (Playwright) | `cd tests/e2e && npm install && npx playwright test` |

## Demo mode

When `DEMO_MODE=true`, adapters use clearly labelled mock data with a visible **Demo data** badge. Mock values are never stored as official evidence.

## Smart Paste URL imports

Paste a complete PropertyGuru or 99.co Singapore listing URL, or copied listing text, into Smart
Paste. URL imports use `/api/v1/sessions/{session_id}/smart-paste`; the browser never calls the
listing site or Groq directly. The API validates the hostname, follows only approved redirects,
applies timeout and response-size limits, extracts metadata/JSON-LD/visible listing text, and
sends bounded evidence to the server-side Groq adapter.

If a listing site returns an access-denied, CAPTCHA, JavaScript-shell or otherwise unusable page,
NearHome uses a bounded headless-browser retry where appropriate, then shows a copy-and-paste
fallback instead of opening an empty review form. Normal Cloudflare CSP references on an otherwise
rendered listing page are not treated as a block. Live URL imports require `DEMO_MODE=false` and
`GROQ_API_KEY`.

## Documentation

- [Architecture](docs/architecture.md)
- [Implementation plan](docs/implementation-plan.md)
- [Implementation status](docs/implementation-status.md)
- [Product specification](docs/nearhome-spec.md)
- [Production deployment](docs/DEPLOYMENT.md)

## Disclaimers

- Fair-price output is an analytical estimate, not an official HDB valuation. The buyer-facing valuation presentation and its diagnostic translation rules are documented in [docs/fair-price-ui.md](docs/fair-price-ui.md).
- Journey estimates are one-way scheduled estimates at your stated departure time — not averages or guarantees.
- School proximity does not guarantee admission.
- Grant-related features are intentionally excluded.

## Current limitations (Phase 1)

- Manual listing entry and immediate comparison work without external APIs.
- Smart Paste, enrichment, CatBoost fair-price valuation with comparable evidence, transport models, and E2E tests are implemented; see the status document for remaining production-data gaps.
- See [implementation status](docs/implementation-status.md) for the full checklist.
