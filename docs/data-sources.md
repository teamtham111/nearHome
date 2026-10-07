# NearHomeV2 data-source inventory

This is the complete source inventory for NearHomeV2. The executable source of truth is
`data_pipeline/source_catalog.py`; print it with:

```bash
apps/api/.venv/bin/python data_pipeline/run_pipeline.py --list-sources
apps/api/.venv/bin/python data_pipeline/run_pipeline.py --list-sources --json
```

NearHome separates source authority from transport. A source can be official while a derived
score is still a NearHome inference. Every external payload or persisted snapshot is validated
before it is mapped into a canonical domain type. Mock/demo values are always labelled as mock
and are never presented as official evidence.

## User and listing evidence

### 1. Buyer input (`buyer-input`)

- Owner/authority: the NearHome user.
- Data: budget, ranked priorities, hard requirements, important locations, named schools,
  confirmed listing facts and user observations.
- Entry: NearHome web forms and API.
- Storage: PostgreSQL records scoped to a comparison session; removed through session deletion.
- Consumers: requirements, scoring, comparison and recommendation engines.

### 2. Public listing text/pages (`listing-pages`)

- Owner/authority: listing publisher or agent; unverified until user confirmation.
- Data: pasted listing text, or bounded metadata, JSON-LD and visible text retrieved from a
  user-supplied public HTTPS listing URL. PropertyGuru and 99.co are the documented user flows.
- Storage: restricted Smart Paste records, source URL and content hash with the session.
- Consumers: Groq extraction followed by explicit user review. Listing claims are never treated
  as government evidence.

### 3. Groq Smart Paste (`groq-smart-paste`)

- Owner: Groq.
- Data sent: bounded cleaned listing evidence only.
- Output: schema-validated extraction candidates, model name, prompt/schema/pipeline versions
  and warnings.
- Credential: `GROQ_API_KEY` (server-side).
- Role: extraction only. Groq cannot choose the NearHome recommendation.
- Docs: <https://console.groq.com/docs>

## Live request-time providers

### 4. OneMap geocoding (`onemap-geocoding`)

- Owner/authority: Singapore Land Authority.
- Data: official address search, standardized address components and WGS84 coordinates.
- Uses: listing geocoding; offline geocoding of MOE school addresses and rail station names.
- Credentials: `ONEMAP_EMAIL`, `ONEMAP_PASSWORD`.
- Docs: <https://www.onemap.gov.sg/apidocs/>

The Next.js `/api/geocode` route and the Python adapter are separate provider boundaries. Both
validate token/search responses. HDB block/street identity and Singapore coordinate bounds are
checked after structural validation.

### 5. Google Places (`google-places`)

- Owner: Google Maps Platform.
- Data: autocomplete suggestions and selected place details.
- Storage: selected Place ID, formatted address and coordinate only.
- Credential: `GOOGLE_MAPS_API_KEY`.
- Consumer: user-confirmed important locations.
- Docs: <https://developers.google.com/maps/documentation/places/web-service>

### 6. Google Routes (`google-routes`)

- Owner: Google Maps Platform.
- Inputs: origin, destination, mode and resolved departure timestamp.
- Outputs: duration, distance, route steps/polyline when needed, alternatives and provider status.
- Uses: personal journeys, walking access, public transport, driving, Major Road access and
  walking routes to candidate carparks.
- Credential: `GOOGLE_MAPS_API_KEY`.
- Storage: evidence in PostgreSQL; optional TTL-bound Redis route cache.
- Docs: <https://developers.google.com/maps/documentation/routes>

### 7. HDB Carpark Availability (`hdb-carpark-availability`)

- Owner/authority: HDB via data.gov.sg.
- Data: point-in-time total/available lots, lot type and source timestamps.
- Endpoint: <https://api.data.gov.sg/v1/transport/carpark-availability>
- Credential: optional `DATA_GOV_SG_API_KEY` for higher limits.
- Cadence: collector every 5–15 minutes; runtime cache defaults to 60 seconds.
- Storage: historical PostgreSQL observations. Stale or missing timestamps are explicitly marked.

## Scheduled official reference snapshots

### 8. HDB resale transactions (`hdb-resale-transactions`)

- Owner/authority: Housing & Development Board via data.gov.sg.
- Dataset: [Resale flat prices from January 2017 onward](https://data.gov.sg/datasets/d_8b84c4ee58e3cfc0ece0d773c8ca6abc/view).
- Dataset ID: `d_8b84c4ee58e3cfc0ece0d773c8ca6abc`.
- Output: `data_pipeline/fixtures/hdb_transactions.json` plus snapshot metadata/checksum.
- Consumers: comparable-sales evidence, CatBoost training and evaluation.
- Cadence: monthly, after data.gov.sg refreshes the dataset.
- Limitation: source prices are indicative transactions, not an official valuation.

### 9. HDB Carpark Information (`hdb-carpark-information`)

- Owner/authority: Housing & Development Board via data.gov.sg.
- Dataset: [HDB Carpark Information](https://data.gov.sg/datasets/d_23f946fa557947f93a8043bbef41dd09/view).
- Dataset ID: `d_23f946fa557947f93a8043bbef41dd09`.
- Data: address, parking type/system, operating attributes, deck count, gantry height and SVY21
  coordinate.
- Transform: EPSG:3414 (SVY21) to EPSG:4326 (WGS84) with `pyproj`.
- Output: `data_pipeline/fixtures/hdb_carparks.json` and an idempotent PostgreSQL mirror.
- Consumer: Home Parking Convenience.

### 10. MOE General information of schools (`moe-school-directory`)

- Owner/authority: Ministry of Education via data.gov.sg.
- Dataset: [General information of schools](https://data.gov.sg/datasets/d_688b934f82c1059ed0a6993d2a829089/view).
- Dataset ID: `d_688b934f82c1059ed0a6993d2a829089`.
- Data: school name, main level, official address and postal code.
- Transform: changed/new addresses are geocoded with OneMap; unchanged addresses reuse validated
  cached coordinates.
- Output: `data_pipeline/fixtures/moe_schools.json` plus snapshot metadata/checksum.
- Consumers: school proximity and named-school requirements.
- Cadence: yearly and after MOE directory changes.

### 11. LTA DataMall BusStops (`lta-bus-stops`)

- Owner/authority: Land Transport Authority.
- Endpoint: `https://datamall2.mytransport.sg/ltaodataservice/BusStops`.
- Data: stop code, description, road and coordinate.
- Output: `data_pipeline/fixtures/lta_bus_stops.json`.
- Credential: `LTA_ACCOUNT_KEY`.

### 12. LTA DataMall BusRoutes (`lta-bus-routes`)

- Owner/authority: Land Transport Authority.
- Endpoint: `https://datamall2.mytransport.sg/ltaodataservice/BusRoutes`.
- Data: ordered stop sequence by service/direction and first/last-bus reference times.
- Output: `data_pipeline/fixtures/lta_bus_routes.json`.
- Credential: `LTA_ACCOUNT_KEY`.

### 13. LTA DataMall BusServices (`lta-bus-services`)

- Owner/authority: Land Transport Authority.
- Endpoint: `https://datamall2.mytransport.sg/ltaodataservice/BusServices`.
- Data: service/direction, operator, category, origin/destination and scheduled frequency ranges.
- Output: `data_pipeline/fixtures/lta_bus_services.json`.
- Credential: `LTA_ACCOUNT_KEY`.

The three LTA bus snapshots are bulk-fetched with OData pagination. NearHome does not issue one
provider request per stop. Their joined index is rejected if stop references, direction/service
keys, sequences or frequency values fail the cross-file quality gate. Official API guide:
<https://datamall.lta.gov.sg/content/datamall/en/dynamic-data.html>.

### 14. Operational MRT/LRT structure (`rail-structure`)

- Authority: NearHome-curated station codes, line order and interchanges, revalidated against the
  [official LTA rail map](https://www.lta.gov.sg/content/ltagov/en/map/train.html).
- Coordinates: OneMap geocoding, not hand-entered.
- Outputs: `rail_lines_structure.csv`, `rail_stations.json` and `rail_edges.csv` under
  `data_pipeline/fixtures/rail/`.
- Cadence: every station/line opening or closure.
- Limitation: LTA DataMall exposes bus topology but no equivalent public RailRoutes topology API;
  this artifact therefore requires an explicit version and human revalidation.

### 15. SLA National Map Line (`sla-national-map-line`)

- Owner/authority: Singapore Land Authority via data.gov.sg.
- Dataset: [National Map Line](https://data.gov.sg/datasets/d_10480c0b59e65663dfae1028ff4aa8bb/view).
- Dataset ID: `d_10480c0b59e65663dfae1028ff4aa8bb`.
- Transform: retain only `FOLDERPATH == "Layers/Major_Road"` LineString/MultiLineString features.
- Output: `data_pipeline/fixtures/sla_major_roads.geojson`.
- Cadence: quarterly or when the source changes.

### 16. OpenStreetMap Singapore drive graph (`openstreetmap-drive-graph`)

- Owner: OpenStreetMap contributors; retrieved offline with OSMnx.
- Data: directed drivable nodes, edges, topology, geometry and road tags.
- Output: `data_pipeline/fixtures/singapore-drive.graphml`.
- Role: supplies routable topology; SLA remains the authority for the Major Road classification.
- Runtime rule: never query Overpass/OSMnx per listing.
- Docs: <https://www.openstreetmap.org/> and <https://osmnx.readthedocs.io/>.

### 17. Google Roads validation (`google-roads-qa`)

- Owner: Google Maps Platform.
- Role: optional, bounded offline corroboration for human QA of the SLA-to-OSM mapping. It is not
  ground truth and is never used as user-facing runtime evidence.
- Credential: `GOOGLE_ROADS_API_KEY`, intentionally separate from runtime Maps configuration.
- Storage: restricted response cache and aggregate validation reports; keys and credential-bearing
  URLs are never written.
- Docs: <https://developers.google.com/maps/documentation/roads/snap>.

## Derived and platform data

### 18. Joined bus index (`bus-index`)

Deterministic join of the three LTA snapshots. The runtime builds an in-memory index; the pipeline
persists `bus_data_quality_report.json`. It is not an independent external source.

### 19. SLA-to-OSM Major Road map (`sla-osm-major-road-map`)

Deterministic spatial/topological mapping of official SLA Major Roads onto routable OSM edges and
legal entry nodes. The versioned JSON contains both parent SHA-256 hashes, mapping algorithm
version and generation timestamp.

### 20. CatBoost fair-price artifact (`catboost-fair-price-model`)

Trained only from the accepted HDB transaction snapshot. Metadata contains the transaction
snapshot key, feature order, cutoff month, training/calibration counts and residual interval.
Runtime inference never trains a model.

### 21. PostgreSQL (`postgresql`)

System of record for sessions, profiles, listings, observations, jobs and persisted evidence.
Supabase PostgreSQL is used in production. This is an internal store, not an external evidence
authority.

### 22. Redis route cache (`redis-route-cache`)

Optional performance cache keyed by a versioned normalized route request. Cache values retain the
underlying provider identity and expire by mode-specific TTL. Redis is not authoritative evidence.

## Validation boundary

```text
external payload / downloaded file / user-confirmed input
        -> provider-specific structural validation
        -> source-specific semantic checks
        -> cross-file/hash/freshness quality gates
        -> canonical NearHome domain data
        -> deterministic feature/model/scoring layer
        -> evidence persisted for comparison
```

Provider contracts live in `apps/api/app/adapters/provider_schemas/`, separate from NearHome's
public FastAPI contracts in `apps/api/app/schemas/`. Models declare every consumed provider field
and tolerate additive fields. A missing or malformed consumed field fails closed. Validation does
not by itself prove correctness or freshness, so coordinate bounds, HDB identity, stale timestamps,
LTA references, duplicate rows, artifact hashes and model/snapshot compatibility have explicit
checks.

## Secrets and privacy

- All provider credentials are server-side variables. Never expose them as `NEXT_PUBLIC_*`.
- Run manifests record credential names, never values.
- Google and OneMap auth headers, Groq payloads and listing raw text are not copied into batch
  manifests.
- Demo adapters use explicit `MOCK_*`/`FIXTURE_*` provenance and visible UI labels.

See [the E2E pipeline runbook](data-pipeline.md) for stage order, profiles, scheduling, manifests,
failure behavior and validation commands.
