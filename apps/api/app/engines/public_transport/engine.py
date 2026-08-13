"""Public Transport model orchestrator.

Access is the only component that converts scheduled frequency into waiting
friction. Coverage, MRT reach and resilience consume its evidence without
re-scoring frequency or walking access.
"""

from __future__ import annotations

from app.adapters.routing.base import RoutingProvider
from app.domain.transport_models import ModelRollup, build_rollup
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
) -> ModelRollup:
    access_result = compute_access(latitude, longitude, routing, config)

    access_value = access_result.value or {}
    walkable_bus_stops = access_value.get("walkable_bus_stops", [])
    practical_rail_entries = access_value.get("practical_rail_entries", [])
    bus_coverage_result = compute_bus_coverage(walkable_bus_stops, config)
    mrt_reach_result = compute_mrt_reach(
        practical_rail_entries,
        config,
    )
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
    return build_rollup(components, config.min_core_weight_coverage)
