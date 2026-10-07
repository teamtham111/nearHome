# NearHome Architecture

## Overview

NearHome is a monorepo decision-support application. Business rules live in the Python **domain** and **engines** layers; the Next.js frontend displays evidence and collects user input.

```mermaid
flowchart TB
  subgraph client [Browser]
    Web[Next.js App Router]
  end

  subgraph api [FastAPI]
    Routes[API Routes /api/v1]
    Services[Services]
    Engines[Deterministic Engines]
    Adapters[External Adapters]
    Jobs[ARQ Workers]
  end

  subgraph data [Data]
    PG[(PostgreSQL)]
    Redis[(Redis)]
  end

  Web --> Routes
  Routes --> Services
  Services --> Engines
  Services --> Adapters
  Services --> PG
  Jobs --> Adapters
  Jobs --> PG
  Adapters --> Redis
```

## Layer boundaries

| Layer | Responsibility |
| --- | --- |
| `domain/` | Enums, dataclasses, invariants |
| `engines/` | Requirements, scoring, recommendation (deterministic) |
| `services/` | Workflows orchestrating repositories and engines |
| `repositories/` | Database access |
| `adapters/` | OneMap, Google, LTA, MOE, HDB, LLM |
| `adapters/provider_schemas/` | Pydantic contracts for external JSON, normalized snapshots and artifact metadata |
| `jobs/` | Enrichment orchestration |
| `schemas/` | Pydantic contracts for NearHome's public API |

## Key architectural decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| ADR-001 | Ranked priorities with 45/35/20 weights | Spec allows ranked or equal; ranked gives clearer UX |
| ADR-002 | Sync SQLAlchemy sessions in Phase 1 | Simpler for learners; async optional later |
| ADR-003 | `DEMO_MODE` with mock adapters | Real interfaces, no fake-as-official data |
| ADR-004 | LLM never selects recommendation | Enforced in engine layer, not UI |
| ADR-005 | Immediate comparison has zero external deps | Spec rule: useful results appear early |
| ADR-006 | Validate external data before canonical mapping | Provider-specific Pydantic models reject malformed consumed fields; semantic and cross-record validation remains explicit |

## End-to-end sequence (manual two-listing flow)

```mermaid
sequenceDiagram
  participant U as User
  participant W as Web
  participant A as API
  participant E as Engines
  participant D as PostgreSQL

  U->>W: Create session
  W->>A: POST /sessions
  A->>D: Insert session
  U->>W: Save buyer profile
  W->>A: PUT /buyer-profile
  U->>W: Confirm listing 1 & 2
  W->>A: POST /listings/manual
  U->>W: View comparison
  W->>A: GET /comparison
  A->>E: Immediate metrics + requirements + scoring
  E-->>A: RecommendationResult
  A-->>W: ComparisonResponse
```

## Repository layout

```text
nearhomev2/
├── apps/api/          FastAPI backend
├── apps/web/          Next.js frontend
├── workers/           Worker entry (via apps/api/app/jobs)
├── data_pipeline/     HDB/MOE/LTA ingestion (Phase 2+)
├── apps/api/app/evaluation/  Deterministic fair-price challenger benchmarks
├── docs/              Product and engineering docs
├── tests/e2e/         Playwright flows (Phase 5)
└── scripts/           Local startup helpers
```

## Security

- Provider keys server-side only
- Rate limiting and request-size limits on Smart Paste (Phase 3)
- Session deletion endpoint removes associated data
- CORS restricted to configured origins

See [security-and-privacy.md](security-and-privacy.md).

## Reference-data plane

All scheduled/offline reference ingestion is orchestrated by
`data_pipeline/run_pipeline.py`. It downloads immutable raw snapshots, validates provider schemas,
builds joined/derived artifacts, applies cross-dataset quality gates and writes a hash-bearing run
manifest. Runtime API/worker processes consume the published local artifacts and never issue a
per-listing OSM or SLA download.

See [the E2E data-pipeline runbook](data-pipeline.md) and
[the complete source inventory](data-sources.md).
