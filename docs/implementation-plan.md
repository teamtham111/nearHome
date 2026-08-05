# NearHome Implementation Plan

Source of truth: [nearhome-spec.md](nearhome-spec.md)

## Phase 0 — Repository audit and architecture ✅ In progress

- [x] Audit empty repository (only spec existed)
- [x] Monorepo scaffold (`apps/api`, `apps/web`, `docs`, `scripts`)
- [x] Docker Compose (PostgreSQL, Redis, API, Web, Worker)
- [x] `.env.example`, `.gitignore`, README
- [x] Architecture, implementation plan, status docs
- [ ] CI pipeline (GitHub Actions)
- [ ] OpenAPI client generation package

## Phase 1 — Stable product skeleton 🔄 In progress

### Domain and persistence
- [x] Domain enums (DataStatus, Provenance, RequirementStatus, etc.)
- [x] Domain dataclasses (BuyerProfile, ConfirmedListing, JourneyEstimate, etc.)
- [x] SQLAlchemy ORM models and initial Alembic migration
- [x] Session repository

### Deterministic engines
- [x] Immediate factual comparison
- [x] Hard-requirement engine with journey-metric rejection
- [x] Preference scoring (ranked priorities, 45/35/20)
- [x] Recommendation engine (ties, near-misses, provisional)

### API (Phase 1 subset)
- [x] Session CRUD + deletion
- [x] Buyer profile upsert
- [x] Manual listing confirmation
- [x] Comparison endpoint
- [x] Health / ready

### Frontend (Phase 1 subset)
- [x] Home + session workspace
- [x] Buyer profile form
- [x] Manual listing form
- [x] Comparison view with always-expanded price and fair-price panels
- [ ] Requirement status strip
- [ ] Mobile card layout

### Tests
- [x] Engine unit tests (pytest)
- [ ] Repository integration tests
- [ ] Frontend component tests
- [ ] Playwright E2E

## Phase 2 — Reliable enrichment

- OneMap geocoding adapter (+ mock)
- HDB transaction ingestion pipeline
- Google Places + Routes adapters
- Journey comparison with route matrix
- Progressive enrichment status polling
- School fixtures

## Phase 3 — Smart Paste and fair price

- Full Smart Paste pipeline (Stages A–I)
- Groq structured extraction + demo adapter
- Median PPSM and weighted comparables
- Weighted-comparable fair-price evaluation with time-based splits

## Phase 4 — Transport and driving depth

- Five-component public transport model
- Driving access, friction, route alternatives
- Parking observations

## Phase 5 — Production polish

- Accessibility (WCAG AA)
- Audit traces UI
- Full Playwright suite
- Deployment docs
- Privacy deletion flows

## Dependency order

```text
Domain → DB → Manual input → Immediate comparison → Requirements → Scoring → Recommendation → UI
         ↓
    Enrichment adapters → Journeys → Fair price → Transport → Polish
         ↓
    Smart Paste (parallel after listing confirmation model stable)
```

## Definition of done tracking

See [implementation-status.md](implementation-status.md) for acceptance criteria mapped to files and tests.
