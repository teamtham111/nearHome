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
from app.domain.transport_models import ComponentResult, ModelRollup, build_rollup, not_assessed
from app.engines.driving.major_road_access import compute_major_road_access, outcome_from_component
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


# --- Provider-separated driving (block benchmarks) ----------------------------
#
# Free, nationwide block measurement: baseline major-road access and parking
# walks via the free routing provider (OneMap). Paid, shortlisted-home
# evidence: route flexibility and estimated peak-hour delay via Google, both
# anchored on the exact entry selected by the baseline measurement.


def compute_driving_baseline(
    latitude: float,
    longitude: float,
    free_routing: RoutingProvider,
    config: DrivingConfig = DRIVING_CONFIG,
    availability_provider: CarparkAvailabilityProvider | None = None,
    history_lookup: Callable[[str, str | None], dict] | None = None,
    listing_address: str | None = None,
) -> list[ComponentResult]:
    """Baseline major-road access and parking; never calls a paid provider."""
    major = compute_major_road_access(latitude, longitude, free_routing, config, baseline=True)
    parking = compute_parking_convenience(
        latitude,
        longitude,
        free_routing,
        config,
        availability_provider=availability_provider,
        history_lookup=history_lookup,
        address=listing_address,
    )
    return [major.result, parking]


def compute_driving_paid_evidence(
    latitude: float,
    longitude: float,
    paid_routing: RoutingProvider,
    major_road_access: ComponentResult,
    config: DrivingConfig = DRIVING_CONFIG,
) -> list[ComponentResult]:
    """Google route flexibility and peak-hour delay for a shortlisted home."""
    outcome = outcome_from_component(major_road_access)
    return [
        compute_route_connectivity(latitude, longitude, paid_routing, outcome, config),
        compute_peak_access_penalty(latitude, longitude, paid_routing, outcome, config),
    ]


def paid_evidence_not_requested(reason: str, config: DrivingConfig = DRIVING_CONFIG) -> list[ComponentResult]:
    return [
        not_assessed("route_connectivity", config.weight_route_connectivity, reason),
        not_assessed("peak_access_penalty", config.weight_peak_access_penalty, reason),
    ]


def assemble_driving_rollup(components: list[ComponentResult], config: DrivingConfig = DRIVING_CONFIG) -> ModelRollup:
    order = ["major_road_access", "route_connectivity", "peak_access_penalty", "parking_convenience"]
    by_name = {component.name: component for component in components}
    return build_rollup([by_name[name] for name in order if name in by_name], config.min_core_weight_coverage)
