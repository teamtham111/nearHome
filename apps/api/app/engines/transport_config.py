"""Configurable weights/thresholds for the Public Transport and Driving
models. Centralised here (rather than scattered magic numbers) per the
spec's "keep thresholds, weights and time periods configurable" rule.

These are deterministic, documented starting points — not statistically
calibrated against Singapore-wide percentile data (that calibration is
explicitly deferred future work, see docs/transport-and-driving-models.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PublicTransportConfig:
    weight_access: float = 0.30
    weight_bus_coverage: float = 0.25
    weight_mrt_reach: float = 0.30
    weight_route_resilience: float = 0.15

    # Haversine pre-filter radii — candidate shortlisting only, never final result.
    bus_stop_prefilter_m: float = 800.0
    mrt_prefilter_m: float = 2500.0

    # Geographic sanity cutoff: beyond this routed walk time, a stop/station
    # is not "practically accessible" even if it is the closest in the fixture.
    max_practical_walk_minutes: float = 20.0

    max_access_points_evaluated: int = 8
    max_rail_entries_evaluated: int = 5
    max_feeder_route_pairs: int = 24

    assessed_frequency_period: str = "AM_PEAK"
    maximum_usable_scheduled_interval_minutes: float = 20.0
    scheduled_wait_proxy_cap_minutes: float = 10.0
    station_entry_minutes: float = 2.0
    pre_rail_transfer_penalty_minutes: float = 6.0
    max_feeder_transfers_before_rail: int = 1
    access_station_tie_margin_generalised_minutes: float = 2.0
    access_score_bands: tuple[tuple[float, float], ...] = (
        (4.0, 95.0),
        (7.0, 88.0),
        (10.0, 80.0),
        (15.0, 68.0),
        (20.0, 55.0),
        (30.0, 38.0),
    )
    access_score_floor: float = 20.0

    reachable_within_minutes_short: float = 30.0
    reachable_within_minutes_long: float = 45.0
    mrt_zero_transfer_saturation: int = 20
    mrt_one_transfer_saturation: int = 45
    mrt_multi_transfer_saturation: int = 20
    mrt_extended_saturation: int = 60

    corridor_overlap_threshold: float = 0.70
    # Score saturates once this many distinct corridors are reached — avoids
    # an unbounded count directly driving an unbounded score.
    direct_corridor_saturation_count: int = 8
    one_transfer_corridor_saturation_count: int = 12

    min_core_weight_coverage: float = 0.6


@dataclass(frozen=True)
class DrivingConfig:
    # Driving Connectivity is destination-independent. A regular destination
    # is assessed separately as a personal journey and has no rollup weight.
    weight_major_road_access: float = 0.30
    weight_route_connectivity: float = 0.25
    weight_peak_access_penalty: float = 0.25
    weight_parking_convenience: float = 0.20

    access_point_prefilter_m: float = 6000.0
    max_access_points_evaluated: int = 6

    independent_max_overlap: float = 0.30
    partially_independent_max_overlap: float = 0.70
    not_practical_penalty_minutes: float = 15.0

    carpark_prefilter_m: float = 500.0
    max_carparks_evaluated: int = 5
    carpark_reasonable_250m: float = 250.0
    max_practical_carpark_walk_minutes: float = 12.0
    parking_walk_score_thresholds: tuple[tuple[float, float], ...] = (
        (2.0, 100.0),
        (4.0, 85.0),
        (6.0, 70.0),
        (8.0, 50.0),
    )
    parking_walk_floor_score: float = 25.0
    driving_time_score_thresholds: tuple[tuple[float, float], ...] = (
        (20.0, 100.0),
        (35.0, 85.0),
        (50.0, 70.0),
        (70.0, 50.0),
    )
    driving_time_floor_score: float = 30.0

    min_core_weight_coverage: float = 0.6

    # AM peak departure hour used for peak-vs-off-peak comparisons (Part 7.3).
    am_peak_hour: int = 8
    off_peak_hour: int = 22


PT_CONFIG = PublicTransportConfig()
DRIVING_CONFIG = DrivingConfig()
