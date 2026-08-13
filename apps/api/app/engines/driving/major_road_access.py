"""Major-road access through actual entries into SLA-designated roads.

The SLA National Map Line ``Layers/Major_Road`` classification determines the
target roads. A locally persisted OSMnx driving graph finds legal, directed
entry junctions; Google Routes measures driving time from the listing to them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.routing.base import RouteResult, RoutingProvider
from app.adapters.routing.batch import RouteCall, run_bounded_route_calls
from app.adapters.transport_data.major_road_network import (
    MajorRoadEntryPoint,
    MajorRoadMapping,
    SlaMajorRoad,
    SlaMajorRoadStore,
    SlaOsmMajorRoadMappingStore,
    find_candidate_sla_major_roads,
)
from app.core.logging import get_logger
from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed
from app.engines.driving.major_road_geometry import validate_sustained_major_road_entry
from app.engines.time_utils import next_occurrence_at_hour
from app.engines.transport_config import DRIVING_CONFIG, DrivingConfig

logger = get_logger(__name__)


@dataclass
class MajorRoadAccessOutcome:
    result: ComponentResult
    selected_point: MajorRoadEntryPoint | None
    selected_route: RouteResult | None
    candidate_points: list[MajorRoadEntryPoint]
    actual_access_coordinate: tuple[float, float] | None = None


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


def _not_assessed(weight: float, explanation: str, limitation: str) -> MajorRoadAccessOutcome:
    result = not_assessed("major_road_access", weight, explanation, limitations=[limitation])
    return MajorRoadAccessOutcome(result, None, None, [])


def compute_major_road_access(
    latitude: float,
    longitude: float,
    routing: RoutingProvider,
    config: DrivingConfig = DRIVING_CONFIG,
    *,
    roads: tuple[SlaMajorRoad, ...] | None = None,
    mapping: MajorRoadMapping | None = None,
) -> MajorRoadAccessOutcome:
    """Use Google routes to precomputed valid OSM-derived Major Road entries."""
    weight = config.weight_major_road_access
    roads = SlaMajorRoadStore.load() if roads is None else roads
    mapping = SlaOsmMajorRoadMappingStore.load() if mapping is None else mapping
    if not roads:
        return _not_assessed(
            weight,
            "Official SLA Major_Road geometry is unavailable.",
            "Refresh the filtered SLA National Map Line artifact before assessing major-road access.",
        )
    if mapping is None:
        return _not_assessed(
            weight,
            "The precomputed SLA-to-OSM Major Road mapping is unavailable or incompatible.",
            "Rebuild the mapping after refreshing the SLA Major Road or Singapore OSM graph artifact.",
        )

    candidate_roads = find_candidate_sla_major_roads(
        roads,
        latitude,
        longitude,
        config.major_road_search_safety_radius_m,
        config.major_road_candidate_limit,
    )
    if not candidate_roads:
        return _not_assessed(
            weight,
            "No SLA-designated Major Road is within the defensive search safety bound.",
            "The bound protects against missing/out-of-area source data; it is not an access-quality threshold.",
        )

    candidates: list[MajorRoadEntryPoint] = []
    for road in candidate_roads:
        precomputed = mapping.road_for(road.identifier)
        if precomputed is None:
            continue
        candidates.extend(precomputed.entry_nodes)
    if not candidates:
        return _not_assessed(
            weight,
            "Nearby SLA Major Roads could not be matched to a valid OSM driving entry junction.",
            "No access is inferred from a nearby line geometry, bridge, or grade-separated crossing.",
        )

    # Local distance is deliberately only an API-cost guard.  It has no role
    # in final selection, which is based on Google duration then distance.
    candidates = sorted(
        candidates,
        key=lambda point: (
            (point.latitude - latitude) ** 2 + (point.longitude - longitude) ** 2,
            point.major_road_id,
            point.candidate_id or point.node_id,
        ),
    )[: config.max_major_road_route_candidates]
    logger.info(
        "major_road_catalogue_candidates_selected",
        catalogue_version=mapping.catalogue_version,
        nearby_sla_road_count=len(candidate_roads),
        catalogue_candidate_count=sum(
            len(mapping.road_for(road.identifier).entry_nodes)
            for road in candidate_roads
            if mapping.road_for(road.identifier) is not None
        ),
        locally_filtered_count=len(candidates),
    )
    departure = next_occurrence_at_hour(config.am_peak_hour)
    calls = [
        RouteCall(
            key=(
                f"major-road:{latitude:.6f}:{longitude:.6f}:{point.routing_coordinate[0]:.6f}:"
                f"{point.routing_coordinate[1]:.6f}:{departure.isoformat()}"
            ),
            call=lambda point=point: routing.get_driving_route_summary(
                (latitude, longitude), point.routing_coordinate, departure, traffic_aware=True
            ),
        )
        for point in candidates
    ]
    routed = [
        (point, outcome.result)
        for point, outcome in zip(candidates, run_bounded_route_calls(calls), strict=True)
        if outcome.result is not None
    ]
    if not routed:
        return _not_assessed(
            weight,
            "Google Routes could not confirm driving access to any valid Major Road entry.",
            "Failed candidate routes are discarded; no OSM-time or geometric fallback is used.",
        )
    routed.sort(
        key=lambda item: (
            item[1].duration_minutes,
            item[1].distance_metres,
            item[0].major_road_id,
            item[0].node_id,
        )
    )
    detailed = routed[: config.major_road_full_polyline_candidate_limit]
    detail_calls = [
        RouteCall(
            key=(
                f"major-road-detail:{latitude:.6f}:{longitude:.6f}:{point.routing_coordinate[0]:.6f}:"
                f"{point.routing_coordinate[1]:.6f}:{departure.isoformat()}"
            ),
            call=lambda point=point: routing.get_driving_alternatives(
                (latitude, longitude), point.routing_coordinate, departure
            ),
        )
        for point, _route in detailed
    ]
    roads_by_identifier = {road.identifier: road for road in candidate_roads}
    validated: list[tuple[MajorRoadEntryPoint, RouteResult, object]] = []
    for (point, _initial_route), outcome in zip(detailed, run_bounded_route_calls(detail_calls), strict=True):
        alternatives = outcome.result or []
        if not alternatives or point.major_road_id not in roads_by_identifier:
            continue
        detailed_route = alternatives[0]
        validation = validate_sustained_major_road_entry(
            detailed_route, roads_by_identifier[point.major_road_id], point, config
        )
        if validation.valid:
            validated.append((point, detailed_route, validation))
    if not validated:
        logger.info(
            "major_road_access_no_sustained_entry",
            catalogue_version=mapping.catalogue_version,
            google_routed_count=len(routed),
            detailed_candidate_count=len(detailed),
        )
        return _not_assessed(
            weight,
            (
                "Google routes reached candidate destinations but none showed sustained travel "
                "on the intended SLA Major Road."
            ),
            (
                "Candidate junctions are hypotheses; crossings, frontage roads, and routes "
                "without usable geometry are rejected."
            ),
        )
    validated.sort(
        key=lambda item: (
            item[1].duration_minutes,
            item[1].distance_metres,
            item[0].major_road_id,
            item[0].candidate_id or item[0].node_id,
        )
    )
    selected_point, selected_route, selected_validation = validated[0]
    logger.info(
        "major_road_access_selected",
        catalogue_version=mapping.catalogue_version,
        selected_sla_road=selected_point.major_road_id,
        selected_candidate=selected_point.candidate_id,
        selected_duration_minutes=selected_route.duration_minutes,
        validated_count=len(validated),
    )
    score = _score_for_minutes(selected_route.duration_minutes)
    evidence = [
        {
            "selected": point == selected_point,
            "sla_major_road_name": point.name,
            "sla_major_road_id": point.major_road_id,
            "osm_entry_node": point.node_id,
            "entry_latitude": point.latitude,
            "entry_longitude": point.longitude,
            "candidate_id": point.candidate_id,
            "catalogue_target_latitude": point.routing_coordinate[0],
            "catalogue_target_longitude": point.routing_coordinate[1],
            "matched_osm_edge_ids": [list(edge_id) for edge_id in point.matched_edge_ids],
            "google_driving_distance_metres": route.distance_metres,
            "google_driving_duration_minutes": route.duration_minutes,
            "provider": route.provider,
            "traffic_aware": route.traffic_aware,
            "sustained_overlap_metres": validation.sustained_overlap_metres,
            "actual_access_latitude": validation.actual_access_latitude,
            "actual_access_longitude": validation.actual_access_longitude,
            "validation_reason": validation.reason,
        }
        for point, route, validation in validated
    ]
    result = ComponentResult(
        name="major_road_access",
        value={
            "selected_access_point": selected_point.name,
            "selected_major_road": selected_point.name,
            "peak_duration_minutes": selected_route.duration_minutes,
            "routed_distance_metres": selected_route.distance_metres,
            "routed_duration_minutes": selected_route.duration_minutes,
            "actual_access_latitude": selected_validation.actual_access_latitude,
            "actual_access_longitude": selected_validation.actual_access_longitude,
            "catalogue_version": mapping.catalogue_version,
        },
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"Fastest Google-routed drive with sustained entry onto SLA Major Road {selected_point.name} "
            f"in approximately {selected_route.duration_minutes:.1f} min."
        ),
        strengths=[
            f"{len(routed)} locally filtered catalogue entrance(s) were duration-ranked; "
            f"{len(validated)} passed sustained SLA-road geometry validation."
        ],
        limitations=[
            (
                "OSM topology supplies the offline entry hypothesis; Google route geometry "
                "validates sustained SLA-road entry."
            ),
        ],
        evidence=evidence,
        source="SLA National Map Line Major_Road + versioned OSM topology catalogue + Google Routes geometry",
        provenance=Provenance.ROUTED_LIVE,
        confidence="high" if selected_route.traffic_aware else "medium",
    )
    return MajorRoadAccessOutcome(
        result,
        selected_point,
        selected_route,
        [point for point, _route, _validation in validated],
        (selected_validation.actual_access_latitude, selected_validation.actual_access_longitude)
        if selected_validation.actual_access_latitude is not None
        and selected_validation.actual_access_longitude is not None
        else None,
    )
