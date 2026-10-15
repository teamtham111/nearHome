"""Peak-hour access penalty — extra time versus off-peak, same origin and access point.

Spec (Part 7.3):
    peak_hour_access_penalty = peak_route_duration - off_peak_route_duration

Reuses the exact access point selected by `major_road_access` — never
compares two different destinations — per the explicit requirement.
"""

from __future__ import annotations

from app.adapters.routing.base import RoutingProvider, RoutingProviderError
from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed, provider_error
from app.engines.driving.major_road_access import MajorRoadAccessOutcome
from app.engines.time_utils import next_occurrence_at_hour, next_weekday_occurrence_at_hour
from app.engines.transport_config import DRIVING_CONFIG, DrivingConfig


def _score_for_penalty(penalty_minutes: float) -> float:
    if penalty_minutes <= 2:
        return 95.0
    if penalty_minutes <= 5:
        return 85.0
    if penalty_minutes <= 10:
        return 70.0
    if penalty_minutes <= 15:
        return 55.0
    if penalty_minutes <= 25:
        return 40.0
    return 25.0


def compute_peak_access_penalty(
    latitude: float,
    longitude: float,
    routing: RoutingProvider,
    major_road_access: MajorRoadAccessOutcome,
    config: DrivingConfig = DRIVING_CONFIG,
) -> ComponentResult:
    weight = config.weight_peak_access_penalty

    if major_road_access.selected_point is None:
        return not_assessed(
            "peak_access_penalty",
            weight,
            "No major-road access point was selected, so the peak-hour penalty cannot be assessed.",
        )

    point = major_road_access.selected_point
    # Both requests go from the same origin to the exact entry selected by
    # baseline major-road access, and both come from the same traffic-aware
    # provider. A baseline (static) duration is never subtracted from a
    # traffic-aware one.
    peak_departure = next_weekday_occurrence_at_hour(config.am_peak_hour)
    off_peak_departure = next_occurrence_at_hour(config.off_peak_hour)

    try:
        peak_route = routing.get_driving_route(
            (latitude, longitude), point.routing_coordinate, peak_departure, traffic_aware=True
        )
        off_peak_route = routing.get_driving_route(
            (latitude, longitude), point.routing_coordinate, off_peak_departure, traffic_aware=True
        )
    except RoutingProviderError:
        return provider_error(
            "peak_access_penalty",
            weight,
            "Routing provider could not confirm peak and off-peak driving durations to the selected access point.",
        )
    if not peak_route.traffic_aware or not off_peak_route.traffic_aware:
        return provider_error(
            "peak_access_penalty",
            weight,
            "Traffic-aware estimates were not confirmed for both departure times, so no delay is reported.",
        )

    penalty_minutes = round(peak_route.duration_minutes - off_peak_route.duration_minutes, 1)
    score = _score_for_penalty(max(0.0, penalty_minutes))

    strengths = []
    limitations = []
    if penalty_minutes <= 3:
        strengths.append("Minimal extra delay from peak-hour traffic to the nearest major-road access point.")
    limitations.append("Estimated peak-hour delay from traffic-aware provider estimates; not a reliability measure.")

    return ComponentResult(
        name="peak_access_penalty",
        value={
            "penalty_minutes": penalty_minutes,
            "peak_duration_minutes": peak_route.duration_minutes,
            "off_peak_duration_minutes": off_peak_route.duration_minutes,
            "peak_departure": peak_departure.isoformat(),
            "off_peak_departure": off_peak_departure.isoformat(),
            "provider": peak_route.provider,
        },
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"Estimated peak-hour delay: driving to {point.name} takes ~{peak_route.duration_minutes:.0f} min "
            f"at the weekday AM peak versus ~{off_peak_route.duration_minutes:.0f} min off-peak "
            f"({penalty_minutes:.1f} min difference)."
        ),
        strengths=strengths,
        limitations=limitations,
        evidence=[
            {
                "selected_access_point": point.name,
                "sla_major_road": point.name,
                "catalogue_target": point.routing_coordinate,
                "off_peak_duration_minutes": off_peak_route.duration_minutes,
                "peak_duration_minutes": peak_route.duration_minutes,
                "penalty_minutes": penalty_minutes,
                "peak_traffic_aware": peak_route.traffic_aware,
                "off_peak_traffic_aware": off_peak_route.traffic_aware,
            }
        ],
        source="Google Routes (driving, traffic-aware, same access point at two departure times)",
        provenance=Provenance.ROUTED_LIVE,
        confidence="high" if peak_route.traffic_aware and off_peak_route.traffic_aware else "medium",
    )
