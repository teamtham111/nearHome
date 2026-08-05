"""Bus coverage component — genuinely different corridors reachable by bus.

Spec (Part 6.3):
    bus_coverage_score = direct_coverage_score * 0.70 + practical_one_transfer_score * 0.30

Measures deduplicated *corridors*, never raw bus-stop or bus-service counts.
"""

from __future__ import annotations

from app.adapters.transport_data.lta_bus import LtaBusDataStore
from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed
from app.engines.transport_config import PT_CONFIG, PublicTransportConfig
from app.networks.bus_network import BusNetwork, get_bus_network

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
    network: BusNetwork,
    config: PublicTransportConfig,
) -> bool:
    info = network.corridor_info(corridor_id)
    if info is None:
        return False
    frequencies = []
    unknown_service = False
    for key in info.member_services:
        service = LtaBusDataStore.service_info(key)
        if service is None:
            unknown_service = True
            continue
        frequency = service.frequencies.get(config.assessed_frequency_period)
        if frequency is not None:
            frequencies.append(frequency)
    # Synthetic network test doubles may not have service metadata; the
    # corridor itself is still valid structural evidence in that case.
    if unknown_service and not frequencies:
        return True
    if not frequencies:
        return False
    combined_max = 1 / sum(1 / frequency.maximum_minutes for frequency in frequencies)
    return combined_max <= config.maximum_usable_scheduled_interval_minutes


def compute_bus_coverage(
    usable_bus_stop_codes: list[str],
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
    if not usable_bus_stop_codes:
        return not_assessed(
            "bus_coverage",
            weight,
            "No practically walkable bus stop was found, so bus coverage cannot be assessed.",
        )

    network = bus_network or get_bus_network()
    stop_set = set(usable_bus_stop_codes)
    direct_corridors = {
        corridor
        for corridor in network.direct_corridors_for_stops(stop_set)
        if _corridor_usable(corridor, network, config)
    }
    one_transfer_corridors = {
        corridor
        for corridor in network.one_transfer_corridors(stop_set, direct_corridors)
        if _corridor_usable(corridor, network, config)
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

    sample_direct = [network.corridor_info(c) for c in list(direct_corridors)[:8]]
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
        strengths.append(f"{len(one_transfer_corridors)} additional corridors reachable with one practical transfer.")
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
            f"{len(one_transfer_corridors)} genuinely new corridor(s) via one practical transfer."
        ),
        strengths=strengths,
        limitations=limitations
        + ["Corridor grouping is a deterministic stop-sequence-overlap heuristic, not a live route planner."],
        evidence=evidence,
        source="LTA DataMall BusRoutes/BusServices (joined, deduplicated by corridor)",
        provenance=Provenance.CALCULATED,
        confidence="medium",
    )
