# Block-level location measurements and benchmarks

NearHome's Public Transport and Driving features describe the building, not
the individual flat. This document covers how they are calculated once per
HDB block, which providers are used for what, how each approved measurement
is stored separately from its comparisons, and how
"Better than X% of HDB blocks in Singapore / in [town]" is produced.

## 1. Block identity

| Item | Implementation |
| --- | --- |
| Block key | `"<BLOCK>|<STREET>"`, e.g. `291|BUKIT BATOK EAST AVENUE 6` (`app/utils/hdb_block_identity.py`). Block number alone is never used. |
| Street normalisation | The shared `canonical_hdb_address_key` normalisation plus expansion of HDB abbreviations (`BT`→`BUKIT`, `NTH`→`NORTH`, `C'WEALTH`→`COMMONWEALTH`, a leading `ST`→`SAINT`, …) so HDB data and listing-portal spellings agree. Fair-price transaction matching is unchanged. |
| Reference registry | `hdb_blocks` table / `data_pipeline/fixtures/hdb_blocks.json`, built from HDB Property Information. |
| Listing → block | `resolve_block` (`app/services/block_features.py`): OneMap postal code first, then block + street from the listing address, then block + street returned by the geocoder. Stored on `confirmed_listings.block_key` and in the `block_identity` enriched field. |
| Unregistered blocks | A listing whose block is not in the registry still gets a block key, so units in the same building share results; it is compared against published groups but never added to them. |
| Starting point | The registry's OneMap coordinate for the block, shared by every unit. |

Registry snapshot used for this work (HDB dataset updated 2026-10-08):
13,392 buildings, 10,817 residential blocks, **10,817 geocoded (100%)** — every
OneMap match had the same block number on an equivalent street; 1,290 needed
abbreviation matching; no two blocks share a coordinate or postal code. Town
membership uses HDB's `bldg_contract_town` codes and the legend HDB publishes
in the dataset metadata (26 towns plus Central Area). Cross-check: 9,723
blocks also appear in resale transactions and all 9,723 carry the same town
name there (0 disagreements).

## 2. Providers and what is shared

| Feature set (`block_location_features`) | Provider | Components | Ranked? |
| --- | --- | --- | --- |
| `public_transport` | OneMap (free) walking + scheduled transit, LTA bus data, curated rail graph | Access, Bus coverage, MRT reach, Route resilience | Yes — five measurements |
| `driving_baseline` | OneMap (free) driving + walking, SLA/OSM major-road catalogue, HDB carpark data | Major-road access (baseline), Parking convenience | Major-road baseline only |
| `driving_google_evidence` | Google (paid), shortlisted homes only | Route flexibility, Estimated peak-hour delay | Never |

Unit- or buyer-specific information (fair price, lease, storey, schools,
named schools, personal journeys) is not stored per block and is unchanged.

**OneMap provider** (`app/adapters/routing/onemap.py`): walk/drive
`total_time` seconds → minutes; transit `duration`/`walkTime`/`transitTime`
seconds and `startTime`/`endTime` epoch milliseconds → Asia/Singapore times;
LTA services matched on `routeShortName` (`158`), never `SBST BUS 158`; bus
and rail legs keep their vehicle type; route geometry, provider and retrieval
time are kept. Itinerary duration runs from its own start, and OneMap's
`waitingTime` is not added, because Access already adds its frequency-based
scheduled wait. Results are labelled scheduled, not live. Driving
alternatives, traffic-aware driving and matrices raise
`RoutingUnavailableError`; when OneMap is not configured every call fails as
a provider error. There is no fallback to a paid provider. Requests are
spaced by `ONEMAP_MIN_REQUEST_INTERVAL_SECONDS` (0.3 s) process-wide, with
retry and backoff on 401/429/5xx.

**Feeder transit time assumption**: Access's feeder routes are requested for
the next weekday at 08:00 Asia/Singapore (`feeder_departure_hour`), matching
the AM-peak frequency period the bus model already uses, so results do not
depend on when a job runs (previously "now").

**Baseline major-road access** (approved definition change): baseline
estimated driving time from the block to a validated entry onto an SLA
Major Road. The catalogue, candidate shortlist (nearest 10 catalogue
entries), sustained-entry geometry validation and selection rule (fastest
validated entry) are unchanged. The OneMap response geometry is validated
directly, so the previous separate Google detail/alternatives request is no
longer needed. The old "validate only the fastest 3" cap existed to limit
paid Google detail requests; with geometry included in every OneMap response
it is removed, and every routed candidate is validated. In the pilot this
changed Bishan Street 12 from 5/10 blocks assessed (the fastest three were
flyover/frontage entries that correctly failed validation) to 10/10, all
entering Braddell Road. A route that does
not show sustained aligned travel on the intended road is rejected; nearby
but unvalidated entries are never accepted. The stored value carries
`measurement = baseline_estimated_driving_time`, `traffic_aware = false`,
provider and the selected/validated entries.

**Google evidence for shortlisted homes**: route flexibility uses Google
alternatives to the validated entries with the existing overlap/practical-
detour logic, and reports alternatives found, independence and extra travel
time, noting that alternatives are not exhaustive. Estimated peak-hour delay
requests Google traffic-aware times at weekday 08:00 and 22:00 from the same
block to the exact entry chosen by the baseline measurement; it never
subtracts a OneMap time from a Google time. If either result is missing or
not traffic-aware, the delay is unavailable — never zero. Controls:
`GOOGLE_DRIVING_EVIDENCE_ENABLED` (true keeps today's workflow), reuse of a
block's stored evidence for `BLOCK_FEATURE_MAX_AGE_DAYS_DRIVING` (7) days and
only while it is anchored on the same baseline entry. Without this evidence
the two components are unavailable and the driving rollup is shown as partial.

**Reuse rules**: a stored block result is reused only when its calculation
version (engine code version + provider + time assumption + configuration
hash) and reference-data fingerprint (hashes/timestamps of the files it
read, plus demo/live mode) match, and it is younger than
`BLOCK_FEATURE_MAX_AGE_DAYS_PUBLIC_TRANSPORT` (30) /
`BLOCK_FEATURE_MAX_AGE_DAYS_DRIVING_BASELINE` (90) days.

## 3. Approved measurements

| Measurement | Source field | Better |
| --- | --- | --- |
| Time to enter the public-transport network | Access `selected_access_path.generalised_access_cost` | Lower |
| Distinct direct bus corridors | Bus coverage `direct_corridors` | Higher |
| Extra bus corridors with one same-stop transfer | Bus coverage `one_transfer_new_corridors` | Higher |
| MRT/LRT stations within 30 rail minutes | MRT reach zero + one + multi-transfer buckets | Higher |
| Independent public-transport fallbacks | Route resilience `independent_units` | Higher |
| Baseline estimated drive to a major road | Major-road access `baseline_duration_minutes` (OneMap baseline only) | Lower |

Each is a separate comparison; there is no overall Public Transport or
Driving ranking and the two bus measurements are never combined.

Status rules: `measured`; `worst` (Access calculated with no practical entry
path — ranks below every value, ties with other such blocks); `unavailable`
(provider error, not assessed, unsupported, or a non-baseline/non-OneMap
major-road result) — never joins a group and is never treated as zero.

Evidence only, never ranked: overall composites, route flexibility (including
distinct major roads and independent routes), estimated peak-hour delay,
parking (walk time/distance, selected and alternative carparks, type,
shelter, restrictions, capacity, live and historical availability), schools
and personal journeys.

Existing-definition consequences (unchanged): Bus coverage is
`not_assessed` when no stop is within 400 m routed walking distance, and MRT
reach is `not_assessed` when Access confirms no practical rail entry; those
blocks are unavailable for those two measurements.

## 4. Comparison rules

- Reference set: every HDB residential block in HDB Property Information,
  independent of listings and search filters; each block counts once.
- Better than X% = `100 × blocks with a strictly worse measurement ÷ valid
  comparison blocks`, floored; the subject block is excluded; ties are never
  worse; 100% only when every other block is strictly worse.
- Suppression: withheld when the group has fewer than 100 (Singapore) or 30
  (town) valid comparison blocks, or when under 50% of the group's
  residential blocks have a valid measurement. Group size, reference size and
  coverage are stored with every result. A benchmark is described as complete
  only at ≥99.9% coverage; otherwise the UI shows the share measured.
- Compatibility: a published version is served only if its measurement
  version, ranking version and every calculation version match the running
  code. Rows outside the approved list are never returned. Results computed
  from a different provider, version or reference fingerprint are excluded
  when a version is built.

## 5. Storage and publishing

| Table | Contents |
| --- | --- |
| `hdb_blocks` | Registry, town, postal code, OneMap coordinate, source timestamp. |
| `block_location_features` | Per block × feature set: measurements, full result (gzip), calculation version, fingerprint, origin, timestamp. |
| `location_benchmark_versions` | One build: ranking/measurement/calculation versions, providers, time assumptions, reference fingerprints and file timestamps, registry snapshot date, coverage summary. `building` → `published` → `superseded`. |
| `location_benchmark_results` | Per block × approved measurement: value, status, Singapore and town comparison. |
| `location_benchmark_cohorts` | Sorted distribution per measurement and group, for listings outside the published results. |

Versions are built from stored measurements only, in one transaction, and
become visible only when flipped to `published`. The comparison API reads the
published version; it never recalculates the national dataset.

## 6. Free backfill and refresh

From `apps/api` with `DATABASE_URL` set (use a disposable database for trials):

```bash
alembic upgrade head
python -m app.jobs.location_benchmarks load-registry       # committed registry fixture
python -m app.jobs.location_benchmarks status
python -m app.jobs.location_benchmarks measure --features public_transport --town BH --limit 50 --max-requests 3000
python -m app.jobs.location_benchmarks measure --features driving_baseline --max-requests 5000
python -m app.jobs.location_benchmarks publish
```

The job only accepts `public_transport` and `driving_baseline`, only uses the
free provider, and refuses to run with anything else, so it makes zero Google
calls. It skips current results, so re-running resumes an interrupted run and
refreshes only what changed after a reference-data update. `--max-requests`
bounds OneMap usage per run. Registry refresh:
`data_pipeline/ingest_hdb_blocks.py --live --persist-db` (reuses earlier
geocodes). The scheduled pipeline (`core`/`all`) refreshes the registry only;
measuring is run explicitly.

Before a nationwide run, confirm OneMap's REST routing quota and terms for
bulk use and persistence. Upper bounds are about 37 OneMap requests per block
for Public Transport and about 15 for the driving baseline.

## 7. Rollout and rollback

| Setting | Effect |
| --- | --- |
| `LOCATION_BENCHMARK_DISPLAY=shadow` (default) | Comparisons returned by the API next to existing scores; UI unchanged. |
| `LOCATION_BENCHMARK_DISPLAY=on` | Approved components show "Better than X% of HDB blocks in Singapore" and "…in [town]" with the raw measurement; flexibility, peak delay and parking are labelled evidence only; "/100" rings are hidden. |
| `LOCATION_BENCHMARK_DISPLAY=off` | No benchmark data in responses. |
| `BLOCK_FEATURE_REUSE_ENABLED=false` | No block association or reuse; every listing is calculated individually. |
| `GOOGLE_DRIVING_EVIDENCE_ENABLED=false` | No Google calls for driving; flexibility and peak delay unavailable. |
| `alembic downgrade 010_session_feedback` | Drops only the new tables and `confirmed_listings.block_key`. |

Enable `on` only after a published version covers enough blocks: until then
comparisons are withheld and the UI says so.
