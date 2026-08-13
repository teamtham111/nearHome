"""Public-transport Access: select one lowest-friction network entry path.

Access is deliberately separate from coverage and rail reach. It measures the
friction to enter the network through a usable bus corridor, a directly walked
rail station, or a practical feeder journey to rail. All candidates are
shortlisted geographically, then confirmed with routed results.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.adapters.reference_data import ReferenceDataStore, haversine_m
from app.adapters.routing.base import RouteResult, RoutingProvider
from app.adapters.routing.batch import RouteCall, run_bounded_route_calls
from app.adapters.transport_data.lta_bus import LtaBusDataStore
from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, provider_error
from app.engines.transport_config import PT_CONFIG, PublicTransportConfig
from app.networks.bus_network import BusNetwork, get_bus_network
from app.networks.rail_graph import RailGraph, get_rail_graph


def _score_access_cost(cost: float, config: PublicTransportConfig) -> float:
    for upper, score in config.access_score_bands:
        if cost <= upper:
            return score
    return config.access_score_floor


def _route_provenance(route: RouteResult) -> Provenance:
    return Provenance.MOCK_DEMO_DATA if route.provider == "MOCK_ROUTING" else Provenance.ROUTED_LIVE


def _frequency_for_corridors(
    stop_code: str,
    config: PublicTransportConfig,
    network: BusNetwork,
) -> list[dict[str, Any]]:
    """Return usable deduplicated corridors and their scheduled wait proxy."""
    if not LtaBusDataStore.is_usable():
        return []
    context = network.corridors_for_boarding_stops({stop_code}, include_one_transfer=False)
    grouped = {
        corridor_id: context.service_keys_at_boarding_stop(corridor_id, stop_code, direct_only=True)
        for corridor_id in context.direct_corridor_ids()
    }

    results: list[dict[str, Any]] = []
    period = config.assessed_frequency_period
    for corridor_id, keys in grouped.items():
        frequencies = [
            info.frequencies[period]
            for key in keys
            if (info := LtaBusDataStore.service_info(key)) is not None and period in info.frequencies
        ]
        if not frequencies:
            continue
        # Combine scheduled dispatch rates for genuinely shared corridor
        # services, then convert the midpoint interval to a waiting proxy.
        min_interval = 1 / sum(1 / f.minimum_minutes for f in frequencies)
        max_interval = 1 / sum(1 / f.maximum_minutes for f in frequencies)
        midpoint = (min_interval + max_interval) / 2
        if max_interval > config.maximum_usable_scheduled_interval_minutes:
            continue
        wait_proxy = min(midpoint / 2, config.scheduled_wait_proxy_cap_minutes)
        results.append(
            {
                "corridor_id": corridor_id,
                "scheduled_frequency_min_minutes": round(min_interval, 1),
                "scheduled_frequency_max_minutes": round(max_interval, 1),
                "scheduled_frequency_midpoint_minutes": round(midpoint, 1),
                "scheduled_wait_proxy_minutes": round(wait_proxy, 1),
                "services": [
                    {"service": key[0], "direction": key[1]} for key in sorted(keys)
                ],
            }
        )
    return sorted(results, key=lambda item: item["scheduled_wait_proxy_minutes"])


def _walk_entry(stop: Any, route: RouteResult, corridor: dict[str, Any]) -> dict[str, Any]:
    wait = corridor["scheduled_wait_proxy_minutes"]
    walk = route.duration_minutes
    return {
        "path_type": "walk_to_bus",
        "access_point_type": "bus_stop",
        "bus_stop_code": stop.stop_code,
        "name": stop.description,
        "corridor_id": corridor["corridor_id"],
        "services": corridor["services"],
        "walk_minutes": walk,
        "walk_distance_metres": route.distance_metres,
        "scheduled_frequency_min_minutes": corridor["scheduled_frequency_min_minutes"],
        "scheduled_frequency_max_minutes": corridor["scheduled_frequency_max_minutes"],
        "scheduled_wait_proxy_minutes": wait,
        "total_expected_minutes": round(walk + wait, 1),
        "generalised_access_cost": round(walk * 1.25 + wait * 1.50, 1),
        "provider": route.provider,
    }


def _direct_rail_entry(
    station: Any, route: RouteResult, config: PublicTransportConfig
) -> dict[str, Any]:
    walk = route.duration_minutes
    return {
        "physical_station_id": station.station_name,
        "access_point_type": "mrt_station",
        "station_name": station.station_name,
        "codes": list(station.codes),
        "lines": list(station.lines),
        "station_codes": list(station.codes),
        "access_mode": "direct_walk",
        "total_expected_minutes": round(walk + config.station_entry_minutes, 1),
        "generalised_access_cost": round(walk * 1.25 + config.station_entry_minutes, 1),
        "walk_minutes": walk,
        "scheduled_wait_proxy_minutes": 0.0,
        "in_vehicle_minutes": 0.0,
        "station_entry_minutes": config.station_entry_minutes,
        "transfers_before_rail": 0,
        "direct_lines": list(station.lines),
        "is_interchange": station.is_interchange,
        "provider": route.provider,
    }


def _feeder_entry(
    stop: Any,
    station: Any,
    walk_route: RouteResult,
    transit_route: RouteResult,
    corridor: dict[str, Any],
    config: PublicTransportConfig,
) -> dict[str, Any] | None:
    transit_steps = [step for step in transit_route.route_steps if step.mode == "TRANSIT"]
    if not transit_steps:
        return None
    transfers = transit_route.transfers
    if transfers is None:
        services = [step.transit_service_number or step.transit_line for step in transit_steps]
        transfers = max(
            0,
            len([service for index, service in enumerate(services) if index and service != services[index - 1]]),
        )
    if transfers > config.max_feeder_transfers_before_rail:
        return None
    walk_from_stop = transit_route.walking_minutes or sum(
        step.duration_minutes for step in transit_route.route_steps if step.mode == "WALK"
    )
    in_vehicle = transit_route.transit_minutes or sum(step.duration_minutes for step in transit_steps)
    wait = corridor["scheduled_wait_proxy_minutes"]
    total = walk_route.duration_minutes + wait + in_vehicle + walk_from_stop + config.station_entry_minutes
    cost = (
        walk_route.duration_minutes * 1.25
        + wait * 1.50
        + in_vehicle
        + walk_from_stop
        + config.station_entry_minutes
        + transfers * config.pre_rail_transfer_penalty_minutes
    )
    feeder_services = [
        {
            "service": step.transit_service_number or step.transit_line,
            "direction": next(
                (
                    service["direction"]
                    for service in corridor["services"]
                    if service["service"] == (step.transit_service_number or step.transit_line)
                ),
                None,
            ),
        }
        for step in transit_steps
    ]
    return {
        "physical_station_id": station.station_name,
        "access_point_type": "mrt_station",
        "station_name": station.station_name,
        "codes": list(station.codes),
        "lines": list(station.lines),
        "station_codes": list(station.codes),
        "access_mode": "feeder_bus",
        "bus_stop_code": stop.stop_code,
        "total_expected_minutes": round(total, 1),
        "generalised_access_cost": round(cost, 1),
        "walk_minutes": round(walk_route.duration_minutes + walk_from_stop, 1),
        "walk_to_boarding_stop_minutes": walk_route.duration_minutes,
        "walk_from_alighting_stop_minutes": round(walk_from_stop, 1),
        "scheduled_wait_proxy_minutes": wait,
        "in_vehicle_minutes": round(in_vehicle, 1),
        "station_entry_minutes": config.station_entry_minutes,
        "transfers_before_rail": transfers,
        "feeder_services": feeder_services,
        "direct_lines": list(station.lines),
        "is_interchange": station.is_interchange,
        "provider": transit_route.provider,
    }


def _failed_component(weight: float, attempts: int, failures: int) -> ComponentResult | None:
    if attempts and failures == attempts:
        return provider_error(
            "access",
            weight,
            "Every required walking/transit routing request failed; no access score was fabricated.",
        )
    return None


def compute_access(
    latitude: float,
    longitude: float,
    routing: RoutingProvider,
    config: PublicTransportConfig = PT_CONFIG,
    rail_graph: RailGraph | None = None,
) -> ComponentResult:
    weight = config.weight_access
    graph = rail_graph or get_rail_graph()
    bus_candidates_all = sorted(
        [
            (stop, haversine_m(latitude, longitude, stop.latitude, stop.longitude))
            for stop in ReferenceDataStore.bus_stops()
            if haversine_m(latitude, longitude, stop.latitude, stop.longitude) <= config.bus_stop_prefilter_m
        ],
        key=lambda item: item[1],
    )
    station_candidates: list[Any] = []
    seen_stations: set[str] = set()
    for code, _distance in graph.nearby_station_codes(latitude, longitude, config.mrt_prefilter_m):
        name = graph.station_name_for_code(code)
        station = graph.station_by_code(code)
        if name and station and name not in seen_stations:
            station_candidates.append(station)
            seen_stations.add(name)
    bus_candidates = [item[0] for item in bus_candidates_all[: config.max_access_points_evaluated]]
    station_candidates = station_candidates[: config.max_rail_entries_evaluated]

    attempts = 0
    failures = 0
    successful_routing = False
    bus_entries: list[dict[str, Any]] = []
    walkable_stop_evidence: list[dict[str, Any]] = []
    feeder_entries: list[dict[str, Any]] = []
    direct_rail_entries: list[dict[str, Any]] = []
    network = get_bus_network(config.corridor_overlap_threshold)

    walk_routes: dict[str, RouteResult] = {}
    walk_targets: list[tuple[str, Any]] = [("bus", stop) for stop in bus_candidates]
    walk_targets.extend(
        ("station", station)
        for station in station_candidates
        if station.latitude is not None and station.longitude is not None
    )
    walk_calls = [
        RouteCall(
            key=f"walk:{latitude:.6f}:{longitude:.6f}:{target.latitude:.6f}:{target.longitude:.6f}",
            call=lambda target=target: routing.get_walking_route(
                (latitude, longitude), (target.latitude, target.longitude)
            ),
        )
        for _kind, target in walk_targets
    ]
    attempts += len(walk_calls)
    for (kind, target), outcome in zip(walk_targets, run_bounded_route_calls(walk_calls), strict=True):
        if outcome.result is None:
            failures += 1
            continue
        route = outcome.result
        successful_routing = True
        if kind == "bus":
            walk_routes[target.stop_code] = route
        elif route.duration_minutes <= config.max_practical_walk_minutes:
            direct_rail_entries.append(_direct_rail_entry(target, route, config))

    usable_corridors_by_stop: dict[str, list[dict[str, Any]]] = {}
    if LtaBusDataStore.is_usable():
        for stop in bus_candidates:
            usable_corridors_by_stop[stop.stop_code] = _frequency_for_corridors(stop.stop_code, config, network)
            if (
                stop.stop_code in walk_routes
                and walk_routes[stop.stop_code].duration_minutes <= config.max_bus_stop_access_walk_minutes
            ):
                walkable_stop_evidence.append(
                    {
                        "path_type": "walk_to_bus_stop",
                        "access_point_type": "bus_stop",
                        "bus_stop_code": stop.stop_code,
                        "name": stop.description,
                        "walk_minutes": walk_routes[stop.stop_code].duration_minutes,
                        "walk_distance_metres": walk_routes[stop.stop_code].distance_metres,
                        "services": [
                            {"service": key[0], "direction": key[1]}
                            for key in sorted(LtaBusDataStore.services_by_stop(stop.stop_code))
                        ],
                        "provider": walk_routes[stop.stop_code].provider,
                    }
                )
                bus_entries.extend(
                    _walk_entry(stop, walk_routes[stop.stop_code], corridor)
                    for corridor in usable_corridors_by_stop[stop.stop_code]
                )

    rail_for_feeders = [
        station
        for station in station_candidates
        if station.latitude is not None and station.longitude is not None
    ]
    feeder_pairs = 0
    feeder_targets: list[tuple[Any, Any, RouteResult, list[dict[str, Any]]]] = []
    for stop in bus_candidates:
        walk_route = walk_routes.get(stop.stop_code)
        if walk_route is None or walk_route.duration_minutes > config.max_bus_stop_access_walk_minutes:
            continue
        corridors = usable_corridors_by_stop.get(stop.stop_code, [])
        if not corridors:
            continue
        for station in rail_for_feeders:
            if feeder_pairs >= config.max_feeder_route_pairs:
                break
            feeder_pairs += 1
            feeder_targets.append((stop, station, walk_route, corridors))
        if feeder_pairs >= config.max_feeder_route_pairs:
            break
    feeder_departure = datetime.now(UTC)
    feeder_calls = [
        RouteCall(
            key=(
                f"transit:{stop.latitude:.6f}:{stop.longitude:.6f}:{station.latitude:.6f}:"
                f"{station.longitude:.6f}:{feeder_departure.isoformat()}"
            ),
            call=lambda stop=stop, station=station: routing.get_transit_route(
                (stop.latitude, stop.longitude),
                (station.latitude, station.longitude),
                feeder_departure,
            ),
        )
        for stop, station, _walk_route, _corridors in feeder_targets
    ]
    attempts += len(feeder_calls)
    for (stop, station, walk_route, corridors), outcome in zip(
        feeder_targets, run_bounded_route_calls(feeder_calls), strict=True
    ):
        if outcome.result is None:
            failures += 1
            continue
        transit_route = outcome.result
        successful_routing = True
        # Scheduled frequency must belong to the boarding stop. If the
        # provider returns a service number, prefer its corridor; when
        # provider naming differs, retain only a verified usable corridor.
        service_names = {
            step.transit_service_number or step.transit_line
            for step in transit_route.route_steps
            if step.mode == "TRANSIT"
        }
        matching = [
            corridor
            for corridor in corridors
            if not service_names
            or any(service["service"] in service_names for service in corridor["services"])
        ]
        if not matching:
            continue
        entry = _feeder_entry(stop, station, walk_route, transit_route, matching[0], config)
        if entry is not None:
            feeder_entries.append(entry)

    all_paths = bus_entries + direct_rail_entries + feeder_entries
    all_evidence = walkable_stop_evidence + all_paths
    failure_result = _failed_component(weight, attempts, failures)
    if failure_result is not None and not successful_routing:
        return failure_result

    if not all_paths:
        return ComponentResult(
            name="access",
            value={
                "selected_access_path": None,
                "best_bus_entry": None,
                "best_direct_rail_entry": None,
                "best_feeder_rail_entry": None,
                "practical_rail_entries": [],
                "walkable_bus_stop_codes": [],
                "usable_bus_stop_codes": [],
            },
            score=config.access_score_floor,
            weight=weight,
            status=ComponentStatus.CALCULATED,
            explanation="No practical bus or rail entry path was confirmed from the listing.",
            limitations=["Absence of practical access is scored as a genuine low result."],
            source="Routed walking/transit + LTA scheduled services + curated rail station list",
            provenance=Provenance.ROUTED_LIVE if successful_routing else Provenance.CALCULATED,
            confidence="low",
        )

    best_bus = min(bus_entries, key=lambda item: item["generalised_access_cost"]) if bus_entries else None
    best_direct = (
        min(direct_rail_entries, key=lambda item: item["generalised_access_cost"])
        if direct_rail_entries
        else None
    )
    best_feeder = min(feeder_entries, key=lambda item: item["generalised_access_cost"]) if feeder_entries else None
    selected = min(all_paths, key=lambda item: item["generalised_access_cost"])
    practical_rail = direct_rail_entries + feeder_entries
    practical_rail.sort(key=lambda item: (item["generalised_access_cost"], item["total_expected_minutes"]))
    if practical_rail:
        best_rail_cost = practical_rail[0]["generalised_access_cost"]
        tie_candidates = [
            entry
            for entry in practical_rail
            if entry["generalised_access_cost"]
            <= best_rail_cost + config.access_station_tie_margin_generalised_minutes
        ]
        primary_rail = min(tie_candidates, key=lambda item: item["total_expected_minutes"])
        for entry in practical_rail:
            entry["is_primary_rail_entry"] = entry is primary_rail
    else:
        primary_rail = None
    value = {
        "selected_access_path": selected,
        "best_bus_entry": best_bus,
        "best_direct_rail_entry": best_direct,
        "best_feeder_rail_entry": best_feeder,
        "practical_rail_entries": practical_rail,
        "primary_rail_entry": primary_rail,
        "walkable_bus_stop_codes": sorted({entry["bus_stop_code"] for entry in walkable_stop_evidence}),
        "walkable_bus_stops": walkable_stop_evidence,
        "usable_bus_stop_codes": sorted({entry["bus_stop_code"] for entry in bus_entries}),
    }
    score = round(_score_access_cost(selected["generalised_access_cost"], config), 1)
    limitations: list[str] = []
    if not direct_rail_entries and not feeder_entries:
        limitations.append("No practical rail-entry path was confirmed; Access relies on bus entry.")
    if failures:
        limitations.append(f"{failures} routing request(s) failed and were excluded from Access.")
    if not LtaBusDataStore.is_usable():
        limitations.append("LTA bus service/frequency data was unavailable; bus paths were not assessed.")
    return ComponentResult(
        name="access",
        value=value,
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"Selected {selected['path_type'] if 'path_type' in selected else selected['access_mode']} entry: "
            f"{selected['total_expected_minutes']:.1f} expected minutes, "
            f"{selected['generalised_access_cost']:.1f} generalised access cost."
        ),
        strengths=[
            "A single lowest-friction network-entry path determines Access; alternatives are retained as evidence."
        ],
        limitations=limitations,
        evidence=all_evidence,
        source="Routed walking/transit + LTA scheduled services + curated rail station list",
        provenance=Provenance.ROUTED_LIVE if successful_routing else Provenance.CALCULATED,
        confidence="high" if selected["generalised_access_cost"] <= 15 else "medium",
    )
