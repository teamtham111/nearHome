"""Bus coverage component — genuinely different corridors reachable by bus.

Spec (Part 6.3):
    bus_coverage_score = direct_coverage_score * 0.70 + same_stop_one_transfer_score * 0.30

Measures deduplicated *corridors*, never raw bus-stop or bus-service counts.
"""

from __future__ import annotations

from typing import Any

from app.adapters.transport_data.lta_bus import LtaBusDataStore
from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed
from app.engines.transport_config import PT_CONFIG, PublicTransportConfig
from app.networks.bus_network import BoardingCorridorContext, BusNetwork, get_bus_network

DIRECT_WEIGHT = 0.70
ONE_TRANSFER_WEIGHT = 0.30


def _bucket_direct(count: int, saturation: int) -> float:
    if count <= 0:
        return 0.0
    ratio = count / saturation
    if ratio >= 1.0:
        return 95.0
    if ratio >= 0.6:
        return 85.0
    if ratio >= 0.35:
        return 72.0
    if ratio >= 0.2:
        return 60.0
    return 45.0


def _bucket_one_transfer(count: int, saturation: int) -> float:
    if count <= 0:
        return 30.0
    ratio = count / saturation
    if ratio >= 1.0:
        return 90.0
    if ratio >= 0.5:
        return 78.0
    if ratio >= 0.25:
        return 62.0
    return 45.0


def _corridor_usable(
    corridor_id: str,
    context: BoardingCorridorContext,
    config: PublicTransportConfig,
    *,
    direct_only: bool,
) -> bool:
    # A corridor can be boardable from several nearby stops. Evaluate each
    # boarding stop independently so a service from another stop never boosts
    # its frequency at the resident's actual boarding stop.
    for stop_code in context.boarding_stops_for_corridor(corridor_id, direct_only=direct_only):
        frequencies = []
        for key in context.service_keys_at_boarding_stop(corridor_id, stop_code, direct_only=direct_only):
            service = LtaBusDataStore.service_info(key)
            if service is None:
                continue
            frequency = service.frequencies.get(config.assessed_frequency_period)
            if frequency is not None:
                frequencies.append(frequency)
        if frequencies:
            combined_max = 1 / sum(1 / frequency.maximum_minutes for frequency in frequencies)
            if combined_max <= config.maximum_usable_scheduled_interval_minutes:
                return True
    return False


def _coverage_stop_codes(
    walkable_bus_stops: list[dict[str, Any]],
    config: PublicTransportConfig,
) -> set[str]:
    """Return the strict bus-coverage catchment from routed Access evidence."""
    return {
        str(stop["bus_stop_code"])
        for stop in walkable_bus_stops
        if stop.get("bus_stop_code")
        and isinstance(stop.get("walk_distance_metres"), (int, float))
        and float(stop["walk_distance_metres"]) <= config.max_bus_coverage_walk_distance_metres
    }


def compute_bus_coverage(
    walkable_bus_stops: list[dict[str, Any]],
    config: PublicTransportConfig = PT_CONFIG,
    bus_network: BusNetwork | None = None,
) -> ComponentResult:
    weight = config.weight_bus_coverage

    if not LtaBusDataStore.is_usable():
        return not_assessed(
            "bus_coverage",
            weight,
            "Bus route/service reference data is unavailable or failed data-quality validation.",
        )
    stop_set = _coverage_stop_codes(walkable_bus_stops, config)
    if not stop_set:
        return not_assessed(
            "bus_coverage",
            weight,
            "No bus stop within the routed walking-distance catchment was found, so bus coverage cannot be assessed.",
        )

    network = bus_network or get_bus_network(config.corridor_overlap_threshold)
    context = network.corridors_for_boarding_stops(stop_set)
    direct_corridors = {
        corridor
        for corridor in context.direct_corridor_ids()
        if _corridor_usable(corridor, context, config, direct_only=True)
    }
    one_transfer_corridors = {
        corridor
        for corridor in context.one_transfer_corridor_ids()
        if _corridor_usable(corridor, context, config, direct_only=False)
    }

    if not direct_corridors:
        return not_assessed(
            "bus_coverage",
            weight,
            "No scheduled bus corridors are recorded at the practically walkable stop(s).",
        )

    direct_score = _bucket_direct(len(direct_corridors), config.direct_corridor_saturation_count)
    transfer_score = _bucket_one_transfer(len(one_transfer_corridors), config.one_transfer_corridor_saturation_count)
    score = round(direct_score * DIRECT_WEIGHT + transfer_score * ONE_TRANSFER_WEIGHT, 1)

    sample_direct = [context.corridor_info(c) for c in list(direct_corridors)[:8]]
    evidence = [
        {
            "corridor_id": info.corridor_id,
            "member_services": [{"service": s[0], "direction": s[1]} for s in sorted(info.member_services)],
            "destination": info.representative_destination,
        }
        for info in sample_direct
        if info
    ]

    strengths = []
    limitations = []
    if len(direct_corridors) >= config.direct_corridor_saturation_count:
        strengths.append(f"{len(direct_corridors)} distinct direct bus corridors — broad direct reach.")
    if one_transfer_corridors:
        strengths.append(f"{len(one_transfer_corridors)} additional corridors reachable with one same-stop transfer.")
    else:
        limitations.append("One-transfer connections do not unlock materially new corridors from this location.")

    return ComponentResult(
        name="bus_coverage",
        value={"direct_corridors": len(direct_corridors), "one_transfer_new_corridors": len(one_transfer_corridors)},
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"{len(direct_corridors)} deduplicated direct bus corridor(s) reachable, plus "
            f"{len(one_transfer_corridors)} genuinely new corridor(s) via one same-stop transfer."
        ),
        strengths=strengths,
        limitations=limitations
        + [
            "One-transfer coverage requires the same LTA bus-stop code; "
            "no walking transfer between nearby stops is assumed.",
            "Corridor grouping is a deterministic stop-sequence-overlap heuristic, not a live route planner.",
        ],
        evidence=evidence,
        source="LTA DataMall BusRoutes/BusServices (joined, deduplicated by corridor)",
        provenance=Provenance.CALCULATED,
        confidence="medium",
    )
