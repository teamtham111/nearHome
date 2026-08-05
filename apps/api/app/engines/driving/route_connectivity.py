"""Route connectivity — genuinely independent driving routes available from the property.

Spec (Part 7.2): request driving alternatives to representative destinations
and classify them by shared-road overlap (`networks/route_overlap.py`).
Reuses the already-routed, distinct-expressway candidate access points from
`major_road_access` as the representative destinations, rather than
re-deriving a separate destination set or falling back to bus-service
counts (explicitly forbidden by Part 14).
"""

from __future__ import annotations

from typing import Any

from app.adapters.routing.base import RoutingProvider, RoutingProviderError
from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed, provider_error
from app.engines.driving.major_road_access import MajorRoadAccessOutcome
from app.engines.time_utils import next_occurrence_at_hour
from app.engines.transport_config import DRIVING_CONFIG, DrivingConfig
from app.networks.route_overlap import classify_alternative

MAX_DESTINATIONS = 3


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
    seen_expressways: set[str] = set()
    for point in major_road_access.candidate_points:
        if point.expressway not in seen_expressways:
            seen_expressways.add(point.expressway)
            destinations.append(point)
        if len(destinations) >= MAX_DESTINATIONS:
            break

    departure = next_occurrence_at_hour(config.am_peak_hour)
    per_destination_evidence: list[dict[str, Any]] = []
    independent_count = 0
    partial_count = 0
    provider_failures = 0
    reached_expressways: set[str] = set()

    for dest in destinations:
        try:
            alternatives = routing.get_driving_alternatives(
                (latitude, longitude), (dest.latitude, dest.longitude), departure
            )
        except RoutingProviderError:
            provider_failures += 1
            continue
        if not alternatives:
            continue
        primary, *alts = alternatives
        reached_expressways.add(dest.expressway)
        classifications = []
        for alt in alts:
            overlap = classify_alternative(
                primary,
                alt,
                config.independent_max_overlap,
                config.partially_independent_max_overlap,
                config.not_practical_penalty_minutes,
            )
            classifications.append(overlap)
            if overlap.classification == "independent":
                independent_count += 1
            elif overlap.classification == "partially_independent":
                partial_count += 1
        per_destination_evidence.append(
            {
                "destination": dest.name,
                "expressway": dest.expressway,
                "primary_duration_minutes": primary.duration_minutes,
                "alternatives_considered": len(alts),
                "classifications": [
                    {
                        "classification": c.classification,
                        "overlap_ratio": c.overlap_ratio,
                        "duration_penalty_minutes": c.duration_penalty_minutes,
                        "shared_roads": c.shared_roads,
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

    distinct_expressways = len(reached_expressways)
    score = round(
        min(
            95.0,
            50.0
            + 12.0 * min(3, distinct_expressways)
            + 6.0 * min(4, independent_count)
            + 3.0 * min(4, partial_count),
        ),
        1,
    )

    strengths = []
    limitations = [
        "Route overlap is estimated from named roads mentioned in each route's turn-by-turn steps, "
        "not exact polyline geometry.",
    ]
    if distinct_expressways >= 2:
        strengths.append(f"Routes reach {distinct_expressways} distinct expressways/arterials.")
    if independent_count:
        strengths.append(f"{independent_count} genuinely independent alternative route(s) found.")
    if partial_count:
        strengths.append(f"{partial_count} partially independent alternative route(s) found.")
    if provider_failures:
        limitations.append(f"{provider_failures} representative destination(s) could not be routed.")

    return ComponentResult(
        name="route_connectivity",
        value={
            "distinct_expressways_reached": distinct_expressways,
            "independent_alternatives": independent_count,
            "partially_independent_alternatives": partial_count,
        },
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"Routed alternatives reach {distinct_expressways} distinct expressway(s)/arterial(s), with "
            f"{independent_count} independent and {partial_count} partially independent alternative route(s)."
        ),
        strengths=strengths,
        limitations=limitations,
        evidence=per_destination_evidence,
        source="Google Routes (driving alternatives) + networks/route_overlap.py classification",
        provenance=Provenance.ROUTED_LIVE,
        confidence="medium",
    )
