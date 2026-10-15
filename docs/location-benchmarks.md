# Block-level location measurements and benchmarks

NearHome's location features (Public Transport, Driving, nearby schools)
describe the building, not the individual flat. This document covers how
they are calculated once per HDB block, how each feature's underlying
measurement is stored separately from its comparisons, and how
"Better than X% of HDB blocks in Singapore / in [town]" is produced.

## 1. Block identity

| Item | Implementation |
| --- | --- |
| Block key | `"<BLOCK>|<STREET>"`, e.g. `291|BUKIT BATOK EAST AVENUE 6` (`app/utils/hdb_block_identity.py`). Block number alone is never used. |
| Street normalisation | The shared `canonical_hdb_address_key` normalisation plus expansion of HDB abbreviations (`BT`→`BUKIT`, `NTH`→`NORTH`, `C'WEALTH`→`COMMONWEALTH`, …) so HDB data and listing-portal spellings agree. Fair-price transaction matching is unchanged. |
| Reference registry | `hdb_blocks` table / `data_pipeline/fixtures/hdb_blocks.json`, built from HDB Property Information. |
| Listing → block | `resolve_block` (`app/services/block_features.py`): OneMap postal code first (most reliable building identifier), then block + street from the listing address, then block + street returned by the geocoder. Stored on `confirmed_listings.block_key` and in the `block_identity` enriched field with the resolution method. |
| Unregistered blocks | A listing whose block is not in the registry still gets a block key from its address, so units in the same building share results; it is compared against published groups but is never added to them. |
| Starting point | The registry's OneMap coordinate for the block. Every unit in the block uses that point; if the block has no registry coordinate, the first unit's geocode is stored with the result and reused by later units. |

## 2. What is shared and what stays per unit

| Shared per block (`block_location_features`) | Kept per listing / buyer |
| --- | --- |
| Public Transport rollup (Access, Bus coverage, MRT reach, Route resilience) | Fair price, lease, storey, floor area |
| Driving rollup (Major-road access, Route connectivity, Peak penalty, Parking) | Named-school distances |
| Nearby schools (counts within 1/2 km, nearest school) | Personal journeys to buyer destinations |
| | Per-listing carpark match records (copied from the shared parking result) |

Each stored result carries a calculation version (engine code version + hash
of its configuration), a reference-data fingerprint (hashes and timestamps of
the LTA, rail, SLA/OSM, carpark or MOE files it read, plus demo/live mode),
the origin coordinate, and the full engine output (gzip) so later units reuse
the evidence without provider calls. A stored result is reused only when the
calculation version and fingerprint match and it is younger than
`BLOCK_FEATURE_MAX_AGE_DAYS_PUBLIC_TRANSPORT` (30) /
`BLOCK_FEATURE_MAX_AGE_DAYS_DRIVING` (7) days, because routed journeys and
traffic change over time. Schools have no age limit.

## 3. Measurements and direction

Measurements are fields the existing engines already produce
(`app/engines/location_benchmarks/measurements.py`). No new metric is
introduced and the bus-reach/corridor logic is unchanged.

| Measurement | Source field | Better |
| --- | --- | --- |
| Time to enter the public-transport network | Access `selected_access_path.generalised_access_cost` | Lower |
| Distinct direct bus corridors | Bus coverage `direct_corridors` | Higher |
| Extra bus corridors with one same-stop transfer | Bus coverage `one_transfer_new_corridors` | Higher |
| MRT/LRT stations within 30 rail minutes | MRT reach zero + one + multi-transfer buckets | Higher |
| Independent public-transport fallbacks | Route resilience `independent_units` | Higher |
| Drive to the nearest major road | Major-road access `routed_duration_minutes` | Lower |
| Distinct major roads reached | Route connectivity `distinct_major_roads_reached` | Higher |
| Independent alternative driving routes | Route connectivity `independent_alternatives` | Higher |
| Extra peak-hour minutes to the major road | Peak access penalty `penalty_minutes` | Lower |
| Walk to the likely carpark | Parking `primary_carpark.walk_minutes` | Lower |
| Schools within 1 km | Schools `schools_within_1km` | Buyer-dependent (neutral wording) |
| Distance to the nearest school | Schools `nearest_school_distance_km` | Buyer-dependent (neutral wording) |

Status rules:

- `measured` — a numeric value.
- `worst` — the engine calculated a genuine "no practical option" (Access
  found no practical bus or rail entry; no school within the 2 km search
  radius). It ranks below every numeric value and ties with other `worst`
  blocks. This matches how the existing engines already score these cases.
- `unavailable` — provider error, not assessed, or missing input. It never
  joins a comparison group and is never treated as zero.

Flagged as not ranked (shown as raw evidence only): the overall Public
Transport and Driving composites (weighted heuristics, not measurements),
live carpark availability (minute-to-minute), partially independent driving
routes (no defensible order versus full independence), named-school distances
and personal journeys (buyer-specific). School proximity is ordered but
whether more/closer is better is a buyer preference, so it is compared with
"More schools within 1 km than…" / "Closer to a school than…" wording instead
of "Better than".

Existing-definition consequences worth knowing (not changed here):
Bus coverage is `not_assessed` when no stop is within 400 m routed walking
distance, and MRT reach is `not_assessed` when Access confirms no practical
rail entry. Those blocks are therefore *unavailable* for those two
measurements and are excluded from the groups, which slightly flatters the
remaining distribution. The Access measurement still ranks them as `worst`.

## 4. Comparison rules

Implemented in `app/engines/location_benchmarks/comparison.py`.

- Reference set: every HDB residential block (`residential = Y`) in HDB
  Property Information — independent of active listings and search filters.
  Each block counts once.
- Town: HDB's `bldg_contract_town` code, with names from the legend HDB
  publishes in the dataset's column metadata (26 towns plus Central Area).
  Cross-check: for every block that also appears in the resale-transaction
  data, the HDB town name matched the transaction town (9,723 blocks, 0
  disagreements at the 2026-10-08 snapshot).
- Better than X% = `100 × blocks with a strictly worse measurement ÷ valid
  comparison blocks`, floored to a whole number. The subject block is
  excluded from its own group; ties are never counted as worse, so identical
  measurements always get identical results. 100% appears only when every
  other block is strictly worse.
- Suppression: a comparison is withheld when the group has fewer than 100
  (Singapore) or 30 (town) valid comparison blocks, or when fewer than 50% of
  the group's residential blocks have a valid measurement. Group size,
  reference size and coverage are stored with every result.
- A benchmark is reported as complete only when national coverage is ≥99.9%;
  otherwise the UI states the share of blocks measured so far.

## 5. Storage and publishing

| Table | Contents |
| --- | --- |
| `hdb_blocks` | Registry, town, postal code, OneMap coordinate, source timestamp. |
| `block_location_features` | Per block × feature set: measurements, full result (gzip), versions, fingerprint, origin, timestamp. |
| `location_benchmark_versions` | One build: ranking/measurement/calculation versions, reference fingerprints and file timestamps, registry snapshot date, coverage summary. Status `building` → `published` → `superseded`. |
| `location_benchmark_results` | Per block × measurement: value, status, Singapore and town comparison (better-than %, strictly worse, ties, group size, coverage). |
| `location_benchmark_cohorts` | Sorted distribution per measurement and group, so listings outside the published results can be compared without rebuilding. |

A version is built entirely from stored measurements in one transaction and
becomes visible only when it is flipped to `published`, so listing views never
mix two refreshes. The comparison API (`location_benchmarks_by_listing`) only
reads the published version; it never recalculates the national dataset.
Changing only the ranking rules means re-running `publish`; measurements are
reused (and re-extracted from the stored result if the extraction changes).

## 6. Running the backfill and refresh

From `apps/api` with `DATABASE_URL` set:

```bash
alembic upgrade head
# 1. Registry: download HDB Property Information and geocode new blocks with OneMap
../../apps/api/.venv/bin/python ../../data_pipeline/ingest_hdb_blocks.py --live --persist-db
#    (or load the committed fixture)
python -m app.jobs.location_benchmarks load-registry
# 2. Offline measurements (no paid API)
python -m app.jobs.location_benchmarks measure --features schools
# 3. Paid routing measurements — Google Routes, explicit opt-in
python -m app.jobs.location_benchmarks measure --features public_transport --allow-paid-routing --limit 200
python -m app.jobs.location_benchmarks measure --features driving --allow-paid-routing --town AMK
# 4. Publish one coherent version from everything stored
python -m app.jobs.location_benchmarks publish
python -m app.jobs.location_benchmarks status
```

`measure` skips blocks whose stored result is current, so re-running it is the
incremental refresh: after a reference-data change the fingerprint changes and
only affected feature sets are re-measured. `refresh` = `measure` + `publish`.
The `core`/`all` pipeline profiles run `refresh-hdb-blocks` and
`refresh-location-benchmarks` (offline features only); paid features are never
run by the scheduled pipeline. `measure` prints the routing calls it made;
upper bounds are about 37 Google calls per block for Public Transport and 30
for Driving.

Listing enrichment also fills the store: the first listing in a block
calculates and saves the block result, later units reuse it.

## 7. Rollout and rollback

| Setting | Effect |
| --- | --- |
| `LOCATION_BENCHMARK_DISPLAY=shadow` (default) | Comparisons are read and returned by the API next to the existing scores; the UI is unchanged. |
| `LOCATION_BENCHMARK_DISPLAY=on` | The comparison page shows "Better than X% of HDB blocks in Singapore" (primary) and "…in [town]" (secondary) with the raw measurement, and hides the "/100" rings and component scores. |
| `LOCATION_BENCHMARK_DISPLAY=off` | No benchmark data in responses. |
| `BLOCK_FEATURE_REUSE_ENABLED=false` | Enrichment calculates every listing from its own geocode, as before. |
| `alembic downgrade 010_session_feedback` | Drops only the new tables and the nullable `block_key` column. |

The recommendation engine and preference scoring still use the existing
component scores; benchmarks are presentation only.
