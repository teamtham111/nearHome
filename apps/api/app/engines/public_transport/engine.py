"""Public Transport model orchestrator.

Access is the only component that converts scheduled frequency into waiting
friction. Coverage, MRT reach and resilience consume its evidence without
re-scoring frequency or walking access.
"""

from __future__ import annotations

from app.adapters.routing.base import RoutingProvider
from app.domain.enums import ComponentStatus
from app.domain.transport_models import ModelRollup, build_rollup, not_assessed
from app.engines.public_transport.access import compute_access
from app.engines.public_transport.bus_coverage import compute_bus_coverage
from app.engines.public_transport.mrt_reach import compute_mrt_reach
from app.engines.public_transport.route_resilience import compute_route_resilience
from app.engines.transport_config import PT_CONFIG, PublicTransportConfig


def compute_public_transport_model(
    latitude: float,
    longitude: float,
    routing: RoutingProvider,
    config: PublicTransportConfig = PT_CONFIG,
    *,
    strict_incomplete: bool = False,
) -> ModelRollup:
    """Compute the Public Transport rollup.

    ``strict_incomplete`` is set only by the block benchmark path: if Access
    still has failed routing requests after its retry, the walkable-stop and
    rail-entry lists it hands on are incomplete, so bus corridor coverage, MRT
    reach and resilience are unavailable instead of measured. The live listing
    comparison leaves it off: it keeps scoring but flags the incomplete data.
    """
    access_result = compute_access(latitude, longitude, routing, config, strict_incomplete=strict_incomplete)

    access_value = access_result.value or {}
    walkable_bus_stops = access_value.get("walkable_bus_stops", [])
    practical_rail_entries = access_value.get("practical_rail_entries", [])
    failed_requests = int(access_value.get("routing_failed_requests") or 0)
    detours = len(access_value.get("walking_detours") or [])
    incomplete_requests = int(access_value.get("routing_incomplete_requests") or failed_requests)
    if strict_incomplete and (access_result.status == ComponentStatus.PARTIALLY_ASSESSED or incomplete_requests):
        causes = []
        if failed_requests:
            causes.append(f"{failed_requests} routing request(s) failed")
        if detours:
            causes.append(f"{detours} routed walk(s) were implausible detours")
        reason = (
            f"Access was incomplete ({' and '.join(causes) or 'incomplete routing'}), so the walkable bus stops "
            "and rail entries are incomplete; this measure is unavailable rather than ranked."
        )
        bus_coverage_result = not_assessed("bus_coverage", config.weight_bus_coverage, reason)
        mrt_reach_result = not_assessed("mrt_reach", config.weight_mrt_reach, reason)
    else:
        bus_coverage_result = compute_bus_coverage(walkable_bus_stops, config)
        mrt_reach_result = compute_mrt_reach(practical_rail_entries, config)
    route_resilience_result = compute_route_resilience(
        bus_coverage_result,
        mrt_reach_result,
        config=config,
        access_result=access_result,
    )

    components = [
        access_result,
        bus_coverage_result,
        mrt_reach_result,
        route_resilience_result,
    ]
    rollup = build_rollup(components, config.min_core_weight_coverage)
    if incomplete_requests and not strict_incomplete and access_result.is_assessed:
        causes = []
        if failed_requests:
            causes.append(f"{failed_requests} routing request(s) failed after one retry")
        if detours:
            causes.append(f"{detours} routed walk(s) were implausible detours and were not used")
        note = (
            f"Based on incomplete routing data: {' and '.join(causes)}, "
            "so the stops and stations considered may be incomplete."
        )
        for component in (bus_coverage_result, mrt_reach_result, route_resilience_result):
            if note not in component.limitations:
                component.limitations.append(note)
        rollup.warnings.append(note)
        rollup.incomplete_routing = True
    return rollup
