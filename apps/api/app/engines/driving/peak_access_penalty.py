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
from app.engines.time_utils import next_occurrence_at_hour
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

    if major_road_access.selected_point is None or major_road_access.selected_route is None:
        return not_assessed(
            "peak_access_penalty",
            weight,
            "No major-road access point was selected, so the peak-hour penalty cannot be assessed.",
        )

    point = major_road_access.selected_point
    peak_route = major_road_access.selected_route
    off_peak_departure = next_occurrence_at_hour(config.off_peak_hour)

    try:
        off_peak_route = routing.get_driving_route(
            (latitude, longitude), (point.latitude, point.longitude), off_peak_departure, traffic_aware=True
        )
    except RoutingProviderError:
        return provider_error(
            "peak_access_penalty",
            weight,
            "Routing provider could not confirm an off-peak driving duration to the selected access point.",
        )

    penalty_minutes = round(peak_route.duration_minutes - off_peak_route.duration_minutes, 1)
    score = _score_for_penalty(max(0.0, penalty_minutes))

    strengths = []
    limitations = []
    if penalty_minutes <= 3:
        strengths.append("Minimal extra delay from peak-hour traffic to the nearest major-road access point.")
    if not peak_route.traffic_aware or not off_peak_route.traffic_aware:
        limitations.append(
            "Traffic-aware routing was not confirmed for one or both requests — penalty may be an estimate."
        )

    return ComponentResult(
        name="peak_access_penalty",
        value={"penalty_minutes": penalty_minutes},
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"Driving to {point.name} takes ~{peak_route.duration_minutes:.0f} min at AM peak versus "
            f"~{off_peak_route.duration_minutes:.0f} min off-peak — a {penalty_minutes:.1f} min penalty."
        ),
        strengths=strengths,
        limitations=limitations,
        evidence=[
            {
                "selected_access_point": point.name,
                "expressway": point.expressway,
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
