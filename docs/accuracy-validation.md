# Accuracy validation boundaries

NearHome separates four kinds of confidence. Passing a test in one category
does not establish the others.

| Category | What the repository validates | What it does not establish |
| --- | --- | --- |
| Algorithm correctness | Deterministic unit and golden cases for graph traversal, bus direction/sequence handling, access thresholds, comparable filtering, score/recommendation rules | That the external source data is complete or current |
| Dataset correctness | Fixture invariants: reference IDs resolve, rail edges use valid station/line relationships, bus routes reference known stops and retain unique ordered sequences | Independent confirmation that every LTA/SLA/OSM/rail source fact is current |
| External-provider correctness | Mocked OneMap/Google request and response validation: coordinate order, Singapore bounds, HDB block/street conflicts, route request parameters, malformed route payloads | A live provider's routing/geocoding result at a particular time |
| End-to-end integration correctness | API/integration tests for persistence, scoped job IDs, JSON-safe comparison data, score scale and frontend request/render contracts | Production database contents or an external provider response not represented by a fixture/mock |

## Golden and invariant tests

- `test_transport_reference_invariants.py` verifies the committed rail and LTA
  bus fixture relationships and structural MRT/LRT golden paths.
- `test_public_transport_engine.py` uses small synthetic services to verify
  routed-walk thresholds, same-stop transfers, direction separation, ordered
  downstream corridors, frequency eligibility and hand-checkable access costs.
- `test_routing_provider.py` validates Google Routes payload construction,
  cache distinctions, alternatives and malformed response handling without a
  live paid request.
- `test_geocoding_accuracy.py` and `test_hdb_address_accuracy.py` validate
  OneMap-coordinate safety and canonical HDB identity handling.

The validation suite must be rerun whenever a rail, bus, routing, geocoder or
scoring source/algorithm changes. Curated rail data and external providers are
never described as independently verified solely because these tests pass.

## Corrections established by the audit

- Sengkang (`STC`) and Punggol (`PTC`) are LRT-centre nodes. Their ride edges
  are labelled `SKLRT` and `PGLRT`; the `NE16↔STC` and `NE17↔PTC` edges remain
  the physical interchange links. This corrects line-path evidence without
  changing the underlying station connectivity.
- A Google Routes HTTP success without a valid duration/distance is treated as
  unavailable. It cannot become a zero-minute route or populate the route
  cache.
- A OneMap result with out-of-Singapore/reversed coordinates or a conflicting
  HDB block/street is rejected. The canonical HDB address identity removes a
  trailing `Singapore` country suffix before exact comparison.

## Optional Google Roads corroboration

`data_pipeline/validate_major_road_google_roads.py` is an offline tool only.
It samples SLA and independently reconstructed matched-OSM geometries at 100 m
intervals, calls Google Roads Snap to Roads only with `--allow-google`, and
compares Google place-ID sequence agreement with SVY21 snapped-path overlap.
Responses are keyed by operation, ordered coordinates and schema version in
the ignored `data_pipeline/cache/google_roads_validation/` cache. It never
runs in pytest, startup, enrichment, or the worker.

```sh
# No API calls; show planned volume for ten roads.
docker compose exec -T api python data_pipeline/validate_major_road_google_roads.py --limit 10

# Explicit live validation; require a separately scoped Roads-enabled key.
docker compose exec -T api sh -c 'GOOGLE_ROADS_API_KEY="$GOOGLE_ROADS_API_KEY" python data_pipeline/validate_major_road_google_roads.py --limit 10 --max-requests 20 --allow-google'
```

`HIGH_CONFIDENCE`, `MEDIUM_CONFIDENCE`, `REVIEW`, `LIKELY_INCORRECT`, and
`UNVALIDATABLE` are deterministic audit classifications, not ground truth.
Google corroboration is independent supporting evidence; human labels in
`major_road_google_roads_gold_labels.json` remain necessary for accuracy,
precision, and recall claims.
