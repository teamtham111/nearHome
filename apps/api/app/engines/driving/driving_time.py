"""Separate regular-destination journey calculation.

This helper is retained for callers that need direct traffic-aware route
details. Its result is not a component of the general Driving Connectivity
rollup.
"""

from __future__ import annotations

from datetime import datetime

from app.adapters.routing.base import RoutingProvider, RoutingProviderError
from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed, provider_error
from app.engines.transport_config import DRIVING_CONFIG, DrivingConfig


def compute_driving_time_to_destinations(
    latitude: float,
    longitude: float,
    routing: RoutingProvider,
    destinations: list[tuple[str, tuple[float, float], datetime]],
    config: DrivingConfig = DRIVING_CONFIG,
) -> ComponentResult:
    # This value is metadata for the standalone result only; it is never
    # passed to the general driving rollup.
    weight = 1.0
    if not destinations:
        return not_assessed(
            "driving_time_to_destinations",
            weight,
            "No important driving destination was provided, so destination driving time cannot be assessed.",
        )

    results: list[dict] = []
    failures = 0
    for label, destination, departure in destinations:
        try:
            route = routing.get_driving_route((latitude, longitude), destination, departure, traffic_aware=True)
        except RoutingProviderError:
            failures += 1
            continue
        results.append(
            {
                "label": label,
                "duration_minutes": route.duration_minutes,
                "distance_metres": route.distance_metres,
                "traffic_aware": route.traffic_aware,
                "departure_time": departure.isoformat(),
            }
        )

    if not results:
        return provider_error(
            "driving_time_to_destinations",
            weight,
            "Routing provider could not confirm driving time to any important destination.",
        )

    scores = [_score_duration(item["duration_minutes"], config) for item in results]
    score = round(sum(scores) / len(scores), 1)
    limitations = []
    if failures:
        limitations.append(f"{failures} important driving destination(s) could not be routed.")
    if not all(item["traffic_aware"] for item in results):
        limitations.append("Traffic-aware routing was not confirmed for every destination.")
    return ComponentResult(
        name="driving_time_to_destinations",
        value={
            "destinations": results,
            "average_duration_minutes": round(sum(i["duration_minutes"] for i in results) / len(results), 1),
        },
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"Average routed driving time to {len(results)} important destination(s) is "
            f"~{sum(i['duration_minutes'] for i in results) / len(results):.0f} minutes."
        ),
        limitations=limitations,
        evidence=results,
        source="Google Routes driving with traffic-aware departure times",
        provenance=Provenance.ROUTED_LIVE,
        confidence="high" if all(item["traffic_aware"] for item in results) else "medium",
    )


def _score_duration(minutes: float, config: DrivingConfig) -> float:
    for cutoff, score in config.driving_time_score_thresholds:
        if minutes <= cutoff:
            return score
    return config.driving_time_floor_score
