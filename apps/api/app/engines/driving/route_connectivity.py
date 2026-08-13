"""Route connectivity — genuinely independent driving routes available from the property.

Spec (Part 7.2): request driving alternatives to representative destinations
and classify them by shared-road overlap (`networks/route_overlap.py`).
Reuses the already-routed, distinct SLA Major Road entry points from
`major_road_access` as the representative destinations, rather than
re-deriving a separate destination set or falling back to bus-service
counts (explicitly forbidden by Part 14).
"""

from __future__ import annotations

from typing import Any

from app.adapters.routing.base import RoutingProvider
from app.adapters.routing.batch import RouteCall, run_bounded_route_calls
from app.adapters.transport_data.major_road_network import SingaporeDriveGraphStore
from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed, provider_error
from app.engines.driving.major_road_access import MajorRoadAccessOutcome
from app.engines.time_utils import next_occurrence_at_hour
from app.engines.transport_config import DRIVING_CONFIG, DrivingConfig
from app.networks.route_overlap import MapMatchResult, classify_alternative, map_match_route

MAX_DESTINATIONS = 3


def _match_evidence(match: MapMatchResult | None) -> dict[str, object] | None:
    if match is None:
        return None
    return {
        "matched_distance_m": match.matched_distance_metres,
        "total_route_distance_m": match.total_route_distance_metres,
        "matched_fraction": match.matched_fraction,
        "ambiguous_fraction": match.ambiguous_fraction,
        "unmatched_fraction": match.unmatched_fraction,
        "discontinuity_fraction": match.discontinuity_fraction,
        "confidence": match.confidence,
    }


def compute_route_connectivity(
    latitude: float,
    longitude: float,
    routing: RoutingProvider,
    major_road_access: MajorRoadAccessOutcome,
    config: DrivingConfig = DRIVING_CONFIG,
) -> ComponentResult:
    weight = config.weight_route_connectivity

    if major_road_access.result.status == ComponentStatus.NOT_ASSESSED or not major_road_access.candidate_points:
        return not_assessed(
            "route_connectivity",
            weight,
            "Major-road access could not be established, so route connectivity cannot be assessed.",
        )

    destinations = []
    seen_major_roads: set[str] = set()
    for point in major_road_access.candidate_points:
        if point.major_road_id not in seen_major_roads:
            seen_major_roads.add(point.major_road_id)
            destinations.append(point)
        if len(destinations) >= MAX_DESTINATIONS:
            break

    departure = next_occurrence_at_hour(config.am_peak_hour)
    graph = SingaporeDriveGraphStore.load()
    per_destination_evidence: list[dict[str, Any]] = []
    independent_count = 0
    partial_count = 0
    provider_failures = 0
    reached_major_roads: set[str] = set()

    calls = [
        RouteCall(
            key=f"drive_alts:{latitude:.6f}:{longitude:.6f}:{dest.routing_coordinate[0]:.6f}:{dest.routing_coordinate[1]:.6f}:{departure.isoformat()}",
            call=lambda dest=dest: routing.get_driving_alternatives(
                (latitude, longitude), dest.routing_coordinate, departure
            ),
        )
        for dest in destinations
    ]
    for dest, outcome in zip(destinations, run_bounded_route_calls(calls), strict=True):
        if outcome.error is not None:
            provider_failures += 1
            continue
        alternatives = outcome.result or []
        if not alternatives:
            continue
        primary, *alts = alternatives
        primary_match = map_match_route(primary, graph)
        reached_major_roads.add(dest.major_road_id)
        classifications = []
        for alt in alts:
            overlap = classify_alternative(
                primary,
                alt,
                config.independent_max_overlap,
                config.partially_independent_max_overlap,
                config.not_practical_penalty_minutes,
                graph=graph,
                primary_match=primary_match,
            )
            classifications.append(overlap)
            if overlap.classification == "independent":
                independent_count += 1
            elif overlap.classification == "partially_independent":
                partial_count += 1
        per_destination_evidence.append(
            {
                "destination": dest.name,
                "sla_major_road": dest.name,
                "primary_duration_minutes": primary.duration_minutes,
                "alternatives_considered": len(alts),
                "classifications": [
                    {
                        "classification": c.classification,
                        "overlap_ratio": c.overlap_ratio,
                        "duration_penalty_minutes": c.duration_penalty_minutes,
                        "shared_roads": c.shared_roads,
                        "overlap_method": c.overlap_method,
                        "evidence_note": c.evidence_note,
                        "geometric_overlap_ratio": c.geometric_overlap_ratio,
                        "primary_map_match": _match_evidence(c.primary_match),
                        "alternative_map_match": _match_evidence(c.alternative_match),
                    }
                    for c in classifications
                ],
            }
        )

    if not per_destination_evidence:
        if provider_failures:
            return provider_error(
                "route_connectivity",
                weight,
                "Routing provider could not confirm driving alternatives to any representative destination.",
            )
        return not_assessed(
            "route_connectivity", weight, "No driving alternatives were returned for any representative destination."
        )

    distinct_major_roads = len(reached_major_roads)
    score = round(
        min(
            95.0,
            50.0 + 12.0 * min(3, distinct_major_roads) + 6.0 * min(4, independent_count) + 3.0 * min(4, partial_count),
        ),
        1,
    )

    strengths = []
    limitations = [
        "Route independence is a deterministic OSM/polyline approximation, not a live road-disruption simulation.",
    ]
    if distinct_major_roads >= 2:
        strengths.append(f"Routes reach {distinct_major_roads} distinct SLA Major Roads.")
    if independent_count:
        strengths.append(f"{independent_count} genuinely independent alternative route(s) found.")
    if partial_count:
        strengths.append(f"{partial_count} partially independent alternative route(s) found.")
    if provider_failures:
        limitations.append(f"{provider_failures} representative destination(s) could not be routed.")

    return ComponentResult(
        name="route_connectivity",
        value={
            "distinct_major_roads_reached": distinct_major_roads,
            "independent_alternatives": independent_count,
            "partially_independent_alternatives": partial_count,
        },
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"Routed alternatives reach {distinct_major_roads} distinct SLA Major Road(s), with "
            f"{independent_count} independent and {partial_count} partially independent alternative route(s)."
        ),
        strengths=strengths,
        limitations=limitations,
        evidence=per_destination_evidence,
        source="Google Routes (driving alternatives) + networks/route_overlap.py classification",
        provenance=Provenance.ROUTED_LIVE,
        confidence="medium",
    )
