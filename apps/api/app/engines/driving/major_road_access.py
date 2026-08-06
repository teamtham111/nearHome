"""Peak-hour major-road access — driving time to a *useful* expressway/arterial entrance.

Spec (Part 7.1): select the useful connection by routed peak-hour driving
duration, never by Haversine distance to the nearest coordinate. Returns
both the scored `ComponentResult` and the selected access point/route so
`route_connectivity.py` and `peak_access_penalty.py` can reuse the exact
same selection instead of re-deriving it (Part 7.3 explicitly requires the
same access point for the peak/off-peak comparison).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.routing.base import RouteResult, RoutingProvider
from app.adapters.routing.batch import RouteCall, run_bounded_route_calls
from app.adapters.transport_data.road_access import RoadAccessPoint, RoadAccessPointStore
from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed, provider_error
from app.engines.time_utils import next_occurrence_at_hour
from app.engines.transport_config import DRIVING_CONFIG, DrivingConfig


@dataclass
class MajorRoadAccessOutcome:
    result: ComponentResult
    selected_point: RoadAccessPoint | None
    selected_route: RouteResult | None
    candidate_points: list[RoadAccessPoint]


def _score_for_minutes(minutes: float) -> float:
    if minutes <= 4:
        return 95.0
    if minutes <= 7:
        return 85.0
    if minutes <= 10:
        return 72.0
    if minutes <= 15:
        return 58.0
    if minutes <= 22:
        return 42.0
    return 25.0


def compute_major_road_access(
    latitude: float,
    longitude: float,
    routing: RoutingProvider,
    config: DrivingConfig = DRIVING_CONFIG,
) -> MajorRoadAccessOutcome:
    weight = config.weight_major_road_access

    if not RoadAccessPointStore.is_usable():
        result = not_assessed(
            "major_road_access",
            weight,
            "The curated major-road access-point dataset is unavailable.",
            limitations=["Run data_pipeline/build_road_access_points.py to (re)generate road_access_points.json."],
        )
        return MajorRoadAccessOutcome(result, None, None, [])

    candidates = RoadAccessPointStore.nearby(
        latitude, longitude, config.access_point_prefilter_m, limit=config.max_access_points_evaluated
    )
    if not candidates:
        result = not_assessed(
            "major_road_access",
            weight,
            "No major-road access point was found within the geographic pre-filter radius.",
        )
        return MajorRoadAccessOutcome(result, None, None, [])

    departure = next_occurrence_at_hour(config.am_peak_hour)
    routed: list[tuple[RoadAccessPoint, RouteResult]] = []
    provider_failures = 0
    calls = [
        RouteCall(
            key=f"drive:{latitude:.6f}:{longitude:.6f}:{point.latitude:.6f}:{point.longitude:.6f}:{departure.isoformat()}",
            call=lambda point=point: routing.get_driving_route(
                (latitude, longitude), (point.latitude, point.longitude), departure, traffic_aware=True
            ),
        )
        for point in candidates
    ]
    for point, outcome in zip(candidates, run_bounded_route_calls(calls), strict=True):
        if outcome.error is not None:
            provider_failures += 1
            continue
        if outcome.result is not None:
            routed.append((point, outcome.result))

    if not routed:
        if provider_failures:
            result = provider_error(
                "major_road_access",
                weight,
                "Routing provider could not confirm a driving route to any nearby major-road access point.",
            )
        else:
            result = not_assessed("major_road_access", weight, "No candidate access points could be routed.")
        return MajorRoadAccessOutcome(result, None, None, candidates)

    routed.sort(key=lambda t: t[1].duration_minutes)
    selected_point, selected_route = routed[0]
    score = _score_for_minutes(selected_route.duration_minutes)

    rejected_evidence = [
        {
            "name": p.name,
            "expressway": p.expressway,
            "direction_label": p.direction_label,
            "peak_duration_minutes": r.duration_minutes,
        }
        for p, r in routed[1:]
    ]
    evidence = [
        {
            "selected": True,
            "name": selected_point.name,
            "expressway": selected_point.expressway,
            "direction_label": selected_point.direction_label,
            "peak_duration_minutes": selected_route.duration_minutes,
            "distance_metres": selected_route.distance_metres,
            "traffic_aware": selected_route.traffic_aware,
            "departure_time": departure.isoformat(),
        },
        *[{**e, "selected": False} for e in rejected_evidence],
    ]

    strengths = []
    limitations = []
    if selected_route.duration_minutes <= 7:
        strengths.append(
            f"{selected_point.name} ({selected_point.expressway}) reachable in "
            f"~{selected_route.duration_minutes:.0f} min at peak."
        )
    if len(routed) > 1:
        strengths.append(f"{len(routed)} candidate access points were compared by routed peak-hour duration.")
    if not selected_route.traffic_aware:
        limitations.append("Traffic-aware routing was not available for this request — duration may be an estimate.")
    if provider_failures:
        limitations.append(f"{provider_failures} candidate access point(s) could not be routed.")

    result = ComponentResult(
        name="major_road_access",
        value={"selected_access_point": selected_point.name, "peak_duration_minutes": selected_route.duration_minutes},
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"Best routed peak-hour connection is {selected_point.name} ({selected_point.direction_label}) "
            f"via {selected_point.expressway}, approximately {selected_route.duration_minutes:.0f} min away."
        ),
        strengths=strengths,
        limitations=limitations,
        evidence=evidence,
        source="Google Routes (driving, traffic-aware) + curated major-road access-point list",
        provenance=Provenance.ROUTED_LIVE,
        confidence="high" if selected_route.traffic_aware else "medium",
    )
    return MajorRoadAccessOutcome(result, selected_point, selected_route, [p for p, _ in routed])
