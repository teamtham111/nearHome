# Data Sources

NearHome integrates official and provider data. All adapters implement typed interfaces, timeouts, retries, caching, and mock variants for `DEMO_MODE`.

| Provider | Purpose | Documentation | Storage policy |
| --- | --- | --- | --- |
| OneMap (SLA) | Listing geocoding, address standardisation | https://www.onemap.gov.sg/apidocs/ | Coordinates cached; audit raw response in restricted field |
| SLA National Map Line | Official `Layers/Major_Road` geometries for Major-road access classification | https://data.gov.sg/datasets/d_10480c0b59e65663dfae1028ff4aa8bb/view | Offline filtered GeoJSON artifact; refreshed periodically |
| OpenStreetMap / OSMnx | Directed local drive graph for Major-road entry/routing topology | https://osmnx.readthedocs.io/ | Offline GraphML artifact; never downloaded per listing |
| Derived SLA→OSM Major Road mapping | Versioned STRtree preprocessing of official SLA Major Roads against the persisted OSM graph | — | Offline JSON with input hashes, matched edge IDs and valid entry nodes; regenerated after SLA/OSM refresh |
| data.gov.sg | HDB resale transactions, property info | https://data.gov.sg/ | Snapshot metadata + cleaned rows in PostgreSQL |
| LTA DataMall | Bus/MRT reference | https://datamall.lta.gov.sg/content/datamall/en.html | Reference snapshots |
| MOE | School locations | https://data.gov.sg (school datasets) | Snapshot with retrieval date |
| Google Places | Important location confirmation | https://developers.google.com/maps/documentation/places/web-service | Place ID + formatted address only |
| Google Routes | Journey estimates | https://developers.google.com/maps/documentation/routes | Duration + provider status; no unnecessary PII |
| Groq | Smart Paste structured extraction | https://console.groq.com/docs | Original paste preserved; extraction metadata stored |

## Demo mode

Mock adapters return data with `Provenance.MOCK_DEMO_DATA` and visible UI badge. Mock responses are never labelled official.

## Cache policies (planned)

| Adapter | TTL | Key components |
| --- | --- | --- |
| OneMap geocode | 30 days | normalised address |
| Google Routes matrix | 24 hours | origins, destination, mode, departure timestamp |
| HDB transactions | Until next snapshot | dataset checksum |

## API keys

All keys are server-side environment variables. Never commit keys or expose them in `NEXT_PUBLIC_*` variables.
