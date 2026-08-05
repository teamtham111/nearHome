# Public Transport Strength — revised scoring reference

NearHome measures general neighbourhood public-transport strength, separate
from personalised important-location journeys. The implemented model has four
weighted components:

| Component | Weight |
| --- | ---: |
| Access | 30% |
| Bus coverage | 25% |
| MRT reach | 30% |
| Route resilience | 15% |

Scheduled bus frequency is not a component. It is used only to decide whether
a bus corridor is usable and to calculate the **scheduled waiting-time proxy**
inside Access. It is never displayed as an independent score.

The overall rollup remains a weight-average of assessed components. At least
60% assessed weight is required before the result can influence
recommendations; unavailable/provider-error components never receive a
fallback score.

## Buyer-facing interpretation

The comparison screen presents a descriptive rating plus a rounded whole-number
score. The shared display bands are:

| Score | Rating |
| ---: | --- |
| 85–100 | Excellent |
| 70–84 | Good |
| 55–69 | Fair |
| 40–54 | Limited |
| 0–39 | Very limited |

The underlying score remains available for calculations and shortlist ranking;
only the rounded score is shown prominently. The default card explains the
relationship between first-mile Access and the downstream Bus Coverage/MRT
Reach network, then places the factor details, alternative routes and model
evidence behind progressive disclosure.

Bus one-transfer counts are translated for ordinary buyers using these display
thresholds: 0 = Limited additional coverage, 1–3 = Some additional coverage,
4–7 = Broad additional coverage, and 8 or more = Very broad additional
coverage. The exact count remains in the supporting details.

MRT station counts are physical stations, not station-line codes. For example,
Bishan has direct North–South and Circle Line service, 51 stations reachable
without a rail transfer and 70 additional physical stations reachable with one
rail transfer in the current graph. “0 additional lines” means that no new
line beyond Bishan’s existing direct lines was found within the one-transfer
definition; it is not a claim that the 70 stations are unreachable.

Route resilience can reach 100 when all five configured structural units are
confirmed: a second access mode, a second physical station, an alternative
rail line and two independent direct bus corridors. This is justified only as
structural network diversity; it is not a prediction of live disruptions.

Personal journeys to buyer-defined destinations remain in the separate
**Your journeys** section and never contribute to the general Public Transport
score.

## Access

**Implementation:** `apps/api/app/engines/public_transport/access.py`

Access evaluates all three entry types and scores only the best practical path:

1. Walk to a usable bus corridor.
2. Walk directly to an MRT/LRT station entrance.
3. Walk to a bus stop, take a practical feeder route to rail, then enter the
   station. At most one pre-rail bus transfer is allowed.

Haversine distance only creates candidate shortlists. Walking and feeder
journeys are confirmed by the routing provider. The result retains the best
path of each type, every practical rail-entry station, rejected paths and
provider/timetable provenance.

For one usable corridor:

```text
scheduled_wait_proxy = midpoint_of_scheduled_interval / 2
```

The proxy is capped and is always labelled scheduled; it is not an exact or
real-time arrival prediction. Generalised access cost is:

```text
walking_minutes × 1.25
+ scheduled_wait_proxy × 1.50
+ in_vehicle_minutes
+ station_entry_minutes
+ pre_rail_transfer_count × 6
```

Starting score bands are configurable:

| Generalised access cost | Score |
| ---: | ---: |
| ≤4 min | 95 |
| ≤7 min | 88 |
| ≤10 min | 80 |
| ≤15 min | 68 |
| ≤20 min | 55 |
| ≤30 min | 38 |
| >30 min | 20 |

Access still chooses its primary practical rail-entry station by lowest
generalised access cost. MRT Reach is deliberately independent: it starts at
the geographically closest active MRT station to the listing coordinates,
using the curated station coordinates and Haversine distance. This is a
network-origin selection, not a walking-time estimate, and it is not limited
by Access's practical-walking pre-filter.

No practical path after successful routing is a calculated low Access score.
If every required routing request fails, Access is `provider_error` with no
score. Successful paths remain usable when only some candidates fail.

## Bus coverage

**Implementation:** `apps/api/app/engines/public_transport/bus_coverage.py`

Bus coverage receives all bus stops confirmed by routed walking, not only the
stop used by the selected Access path. Only corridors with valid scheduled
frequency within the configured maximum usability interval count as usable.

Services are deduplicated into corridors using:

```text
shared_stops / min(len(route_a), len(route_b)) >= 0.70
```

The score is:

```text
direct_coverage_score × 0.70
+ practical_one_transfer_score × 0.30
```

Frequency does not add points. It is only the usability gate.

## MRT reach

**Implementation:** `apps/api/app/engines/public_transport/mrt_reach.py`

MRT Reach starts at the single geographically closest active MRT station to
the listing coordinates. It measures rail-network reach from that station;
walking, feeder duration, waiting and station-entry time are not part of this
score and remain in Access. Therefore, a listing can have a low Access result
because no rail entry was practically confirmed while still receiving a
calculated MRT Reach score based on its nearest station.

The rail graph has one node per station-line code, but scores collapse those
codes to one physical station. Each reachable physical station is placed in
exactly one bucket:

| Bucket | Definition |
| --- | --- |
| `zero_transfer_30` | 0 rail transfers, ≤30 structural minutes |
| `one_transfer_30_incremental` | exactly 1 transfer, ≤30 minutes, excluding bucket A |
| `multi_transfer_30_incremental` | 2+ transfers, ≤30 minutes, excluding A/B |
| `extended_31_to_45_incremental` | >30 and ≤45 minutes, excluding all 30-minute buckets |

The starting formula is:

```text
zero_transfer_score × 0.35
+ one_transfer_score × 0.35
+ multi_transfer_score × 0.10
+ extended_score × 0.20
```

Interchange status, direct lines and additional one-transfer lines remain
explanatory evidence. They are not additive bonuses. Access-confirmed
practical stations are retained as context for Route Resilience and do not
change the MRT Reach origin or increase its score.

An unavailable rail graph, or a graph with no active station having valid
coordinates, is `not_assessed`. A nearest station remains a valid MRT Reach
origin even when Access reports bus-only practical access.

## Route resilience

**Implementation:** `apps/api/app/engines/public_transport/route_resilience.py`

Resilience counts independent fallbacks rather than reusing raw bus, line or
station counts. Possible units are:

- a second practical access mode (bus and rail);
- a second physical rail station;
- an alternative rail line unavailable at the primary station;
- a first independent bus corridor;
- a second independent bus corridor.

The score is a configurable deterministic starting heuristic over those units.
The evidence identifies each unit and the primary station/corridor context.
It is not a live disruption simulation.

## Data and failure handling

| Situation | Result |
| --- | --- |
| No nearby candidates | Access calculated with a genuine low score |
| Some routing calls fail, another path succeeds | Score successful paths and expose limitations |
| Every required routing call fails | `provider_error`, score `None` |
| Rail graph unavailable | MRT Reach `not_assessed` |
| No practical rail entry | Access may be calculated low; MRT Reach still uses the nearest active station |
| Missing/invalid scheduled frequency | That bus corridor is not usable |

All routed values identify the routing provider. LTA frequency is a scheduled
range, not real-time reliability. Structural rail minutes are approximate
graph weights, not live train timetables.

## Source index

- Access: `apps/api/app/engines/public_transport/access.py`
- Bus coverage: `apps/api/app/engines/public_transport/bus_coverage.py`
- MRT Reach: `apps/api/app/engines/public_transport/mrt_reach.py`
- Route resilience: `apps/api/app/engines/public_transport/route_resilience.py`
- Rollup: `apps/api/app/domain/transport_models.py`
- Config: `apps/api/app/engines/transport_config.py`
- Tests: `apps/api/app/tests/test_public_transport_engine.py`
