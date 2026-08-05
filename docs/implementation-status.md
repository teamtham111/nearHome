# NearHome Implementation Status

Last updated: full-spec build continuation

## Summary

| Phase | Status |
| --- | --- |
| 1 — Core skeleton | ✅ Complete |
| 2 — Enrichment | ✅ Geocoding, lease, fair price, journeys, schools |
| 3 — Smart Paste + fair price | ✅ Reconciliation + CatBoost/evidence UI |
| 4 — Transport + driving | ✅ Four-component PT; four-component destination-independent Driving Connectivity plus optional personal journeys |
| 5 — Production polish | ✅ Worker, live adapters, rate limits, observations, audit trace API |

**Manual setup:** see [SETUP.md](./SETUP.md)

## New in this build

| Item | Status | Files |
| --- | --- | --- |
| LTA/MRT/MOE reference fixtures | ✅ | `data_pipeline/fixtures/*.json` |
| Reference data store | ✅ | `app/adapters/reference_data.py` |
| Public transport (5 components from coords) | ✅ | `app/engines/public_transport.py` |
| Driving Connectivity + optional destination journeys | ✅ | `app/engines/driving/engine.py`, `app/services/enrichment_service.py`, `regular_destination_journeys` API field |
| Official HDB carpark static/live data | ✅ | `app/adapters/parking/*`, `data_pipeline/ingest_hdb_carparks.py`, `data_pipeline/ingest_hdb_availability.py`, migration `003_hdb_carpark_data` |
| Schools engine | ✅ | `app/engines/schools.py` |
| Observations API | ✅ | `app/services/observation_service.py`, routes |
| Smart Paste reconciliation + evidence | ✅ | `app/services/smart_paste/reconciliation.py` |
| Recommendation audit trace API | ✅ | `GET .../recommendation-trace` |
| Rate limiting (Smart Paste / enrichment) | ✅ | `app/core/rate_limit.py` |
| LTA ingest script | ✅ | `data_pipeline/ingest_lta_reference.py` |
| CatBoost fair-price valuation | ✅ | `app/engines/fair_price.py`, `app/engines/fair_price_catboost.py`, `app/engines/fair_price_comparables.py` |
| Schools + observations UI | ✅ | `comparison-view.tsx`, session page |
| Setup guide | ✅ | `docs/SETUP.md` |

## Remaining vs full spec (honest gaps)

| Area | Gap |
| --- | --- |
| Fair price model | ✅ CatBoost selected for production; weighted-comparable evidence and fallback retained |
| Fair-price challenger evaluation | ✅ | `app/evaluation/`, full-dataset reports under `evaluation_outputs/`; benchmark selection is recorded separately from runtime deployment |
| OneMap pedestrian routing | Haversine walk estimate, not routed paths |
| Driving peak sampling | Single requested departure per destination/access comparison; historical congestion sampling is future work |
| LTA island-wide | Demo fixture unless `--live` ingest run |
| Smart Paste Stages A–I | Core pipeline done; not every spec subsection |
| LLM recommendation wording | Deterministic templates only |
| OpenAPI typed client | Not generated |
| Full WCAG AA audit | Basic labels only |
| Conformal prediction intervals | 🔄 Robust comparable quantiles and model-disagreement widening are implemented; conformal residual calibration remains future work |

## Run tests

```bash
cd apps/api && pytest -q
cd apps/web && npm test && npm run typecheck
```

See [SETUP.md](./SETUP.md) for integration and E2E commands.
