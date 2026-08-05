"""Driving model orchestrator — replaces `engines/driving.py`.

Runs `major_road_access` first since `route_connectivity` and
`peak_access_penalty` both reuse its routed access-point selection (never
re-deriving or comparing against a different access point), then rolls the
four components up with recommendation gating.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.adapters.parking.hdb_availability import CarparkAvailabilityProvider
from app.adapters.routing.base import RoutingProvider
from app.domain.transport_models import ModelRollup, build_rollup
from app.engines.driving.major_road_access import compute_major_road_access
from app.engines.driving.parking_convenience import compute_parking_convenience
from app.engines.driving.peak_access_penalty import compute_peak_access_penalty
from app.engines.driving.route_connectivity import compute_route_connectivity
from app.engines.transport_config import DRIVING_CONFIG, DrivingConfig


def compute_driving_model(
    latitude: float,
    longitude: float,
    routing: RoutingProvider,
    config: DrivingConfig = DRIVING_CONFIG,
    destination_requests: list[tuple[str, tuple[float, float], datetime]] | None = None,
    availability_provider: CarparkAvailabilityProvider | None = None,
    history_lookup: Callable[[str, str | None], dict] | None = None,
    listing_address: str | None = None,
) -> ModelRollup:
    major_road_access_outcome = compute_major_road_access(latitude, longitude, routing, config)
    route_connectivity_result = compute_route_connectivity(
        latitude, longitude, routing, major_road_access_outcome, config
    )
    peak_access_penalty_result = compute_peak_access_penalty(
        latitude, longitude, routing, major_road_access_outcome, config
    )
    parking_convenience_result = compute_parking_convenience(
        latitude,
        longitude,
        routing,
        config,
        availability_provider=availability_provider,
        history_lookup=history_lookup,
        address=listing_address,
    )

    # `destination_requests` remains an accepted compatibility argument for
    # callers that have not yet migrated, but it is deliberately ignored.
    # Personal destination journeys are not general driving-connectivity
    # evidence and must never affect its numerator, denominator, coverage or
    # recommendation eligibility.
    components = [
        major_road_access_outcome.result,
        route_connectivity_result,
        peak_access_penalty_result,
        parking_convenience_result,
    ]
    return build_rollup(components, config.min_core_weight_coverage)
