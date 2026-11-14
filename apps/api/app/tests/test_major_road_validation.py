"""Major-road entry validation: geometry rule, far-target filter, batch widening, diagnostics.

All geometry is synthetic. One degree of longitude near 1.3 N is about 111.3 km,
so 0.0001 degrees is roughly 11 m.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.adapters.routing.base import RouteResult, RoutingProvider, RoutingUnavailableError
from app.adapters.transport_data.major_road_network import (
    MajorRoadEntryPoint,
    MajorRoadMapping,
    PrecomputedMajorRoad,
    SlaMajorRoad,
    SlaMajorRoadStore,
    SlaOsmMajorRoadMappingStore,
)
from app.domain.enums import ComponentStatus
from app.engines.driving.major_road_access import compute_major_road_access
from app.engines.driving.major_road_geometry import validate_sustained_major_road_entry
from app.engines.transport_config import DrivingConfig
from app.tests.routing_helpers import _encode_polyline

ROAD_LAT = 1.3000
ROAD = SlaMajorRoad("sla-r", "Test Expressway", (((103.8000, ROAD_LAT), (103.8400, ROAD_LAT)),))
CONFIG = DrivingConfig()


def entry(target_lon: float, target_lat: float = ROAD_LAT, name: str = "Test Expressway") -> MajorRoadEntryPoint:
    return MajorRoadEntryPoint(
        name=name,
        major_road_id="sla-r",
        node_id=f"n-{target_lon:.5f}",
        latitude=target_lat,
        longitude=target_lon,
        matched_edge_ids=(),
        candidate_id=f"c-{target_lon:.5f}-{target_lat:.5f}",
        target_latitude=target_lat,
        target_longitude=target_lon,
    )


def route(points: list[tuple[float, float]]) -> RouteResult:
    return RouteResult(5.0, 1000, None, None, [], "TEST", None, None, False, encoded_polyline=_encode_polyline(points))


class TestValidator:
    def test_accepted_by_sustained_overlap(self) -> None:
        result = validate_sustained_major_road_entry(
            route([(ROAD_LAT, 103.8010), (ROAD_LAT, 103.8030)]), ROAD, entry(103.8030)
        )
        assert result.valid and result.reason == "sustained_aligned_sla_overlap"
        assert result.sustained_overlap_metres >= 60

    def test_accepted_by_trailing_overlap_plus_shortfall(self) -> None:
        # Joins the road from the south, runs ~55 m along it, and stops ~55 m before the target.
        result = validate_sustained_major_road_entry(
            route([(1.3030, 103.8040), (ROAD_LAT, 103.8040), (ROAD_LAT, 103.80450)]), ROAD, entry(103.8050)
        )
        assert result.valid
        assert result.reason == "aligned_overlap_reaches_target_within_end_shortfall"
        assert result.trailing_overlap_metres >= CONFIG.major_road_min_trailing_overlap_m
        assert 40 < result.route_end_shortfall_metres <= CONFIG.major_road_max_route_end_shortfall_m

    def test_rejected_crossing(self) -> None:
        result = validate_sustained_major_road_entry(
            route([(1.2990, 103.8050), (1.3010, 103.8050)]), ROAD, entry(103.8050)
        )
        assert not result.valid and result.reason == "no_sustained_sla_major_road_overlap"
        assert result.trailing_overlap_metres == 0 and result.sustained_overlap_metres == 0

    def test_rejected_parallel_frontage_road_outside_the_buffer(self) -> None:
        # 33 m north of the road for 330 m: runs beside it, never on it.
        result = validate_sustained_major_road_entry(
            route([(1.30030, 103.8010), (1.30030, 103.8040)]), ROAD, entry(103.8040)
        )
        assert not result.valid
        assert result.closest_approach_metres is not None and result.closest_approach_metres > 18

    def test_rejected_when_shortfall_exceeds_the_maximum(self) -> None:
        # ~55 m aligned (passes the trailing minimum) but the target is ~220 m further on.
        result = validate_sustained_major_road_entry(
            route([(1.3030, 103.8040), (ROAD_LAT, 103.8040), (ROAD_LAT, 103.80450)]), ROAD, entry(103.8065)
        )
        assert result.route_end_shortfall_metres > CONFIG.major_road_max_route_end_shortfall_m
        assert not result.valid

    def test_rejected_when_trailing_run_is_too_short(self) -> None:
        # 30 m on the road then 55 m to the target: the former 20 m minimum accepted this.
        short = route([(1.3030, 103.8040), (ROAD_LAT, 103.8040), (ROAD_LAT, 103.80427)])
        assert not validate_sustained_major_road_entry(short, ROAD, entry(103.8047)).valid
        relaxed = DrivingConfig(major_road_min_trailing_overlap_m=20.0)
        assert validate_sustained_major_road_entry(short, ROAD, entry(103.8047), relaxed).valid

    def test_rejected_when_the_route_end_is_beside_the_road_not_on_it(self) -> None:
        # A side road 12 m north: inside the 18 m buffer and aligned, but not on the carriageway.
        beside = route([(1.30011, 103.8040), (1.30011, 103.80445)])
        assert not validate_sustained_major_road_entry(beside, ROAD, entry(103.8051)).valid
        assert validate_sustained_major_road_entry(
            beside, ROAD, entry(103.8051), DrivingConfig(major_road_end_max_road_distance_m=18.0)
        ).valid

    def test_rejected_when_the_gap_to_the_target_leaves_the_road(self) -> None:
        # Ends on the road after ~55 m but the target is ~100 m away at right angles to it.
        result = validate_sustained_major_road_entry(
            route([(1.3030, 103.8040), (ROAD_LAT, 103.8040), (ROAD_LAT, 103.80450)]),
            ROAD,
            entry(103.80450, target_lat=1.30090),
        )
        assert result.route_end_shortfall_metres <= CONFIG.major_road_max_route_end_shortfall_m
        assert not result.valid

    def test_diagnostics_are_populated_for_rejected_routes(self) -> None:
        result = validate_sustained_major_road_entry(
            route([(1.2990, 103.8050), (1.3010, 103.8050)]), ROAD, entry(103.8070)
        )
        assert result.route_points == 2
        assert result.route_end_shortfall_metres is not None and result.route_end_shortfall_metres > 100
        assert result.closest_approach_metres is not None


class _BatchProvider(RoutingProvider):
    """Baseline routes: along the road for ``good`` target longitudes, crossing it otherwise."""

    provider_name = "BATCH_STUB"

    def __init__(self, good: set[float]) -> None:
        self.good = good
        self.baseline_destinations: list[tuple[float, float]] = []
        self.driving_destinations: list[tuple[float, float]] = []

    def _route(self, destination: tuple[float, float]) -> RouteResult:
        lon = destination[1]
        if round(lon, 5) in self.good:
            return route([(ROAD_LAT, lon - 0.0015), (ROAD_LAT, lon)])
        return route([(1.2990, lon), (1.3010, lon)])

    def get_baseline_driving_route(self, origin, destination):  # type: ignore[no-untyped-def]
        self.baseline_destinations.append(destination)
        return self._route(destination)

    def get_driving_route(self, origin, destination, departure_time: datetime, traffic_aware: bool = True):  # type: ignore[no-untyped-def]
        self.driving_destinations.append(destination)
        return self._route(destination)

    def get_driving_alternatives(self, origin, destination, departure_time):  # type: ignore[no-untyped-def]
        return [self.get_driving_route(origin, destination, departure_time)]

    def get_walking_route(self, origin, destination):  # type: ignore[no-untyped-def]
        raise RoutingUnavailableError("n/a", self.provider_name)

    def get_transit_route(self, origin, destination, departure_time):  # type: ignore[no-untyped-def]
        raise RoutingUnavailableError("n/a", self.provider_name)

    def get_route_matrix(self, origins, destinations, mode, departure_time=None):  # type: ignore[no-untyped-def]
        raise RoutingUnavailableError("n/a", self.provider_name)


ORIGIN = (1.3004, 103.8000)
LONGITUDES = [round(103.8020 + 0.003 * i, 5) for i in range(9)]  # nearest first
BATCH = 3
BATCH_CONFIG = DrivingConfig(max_major_road_route_candidates=BATCH, major_road_max_route_candidate_batches=3)


def mapping_with(entries: list[MajorRoadEntryPoint]) -> MajorRoadMapping:
    road = PrecomputedMajorRoad("sla-r", "Test Expressway", (), tuple(entries), 1)
    return MajorRoadMapping("g", "s", "t", "test", 100.0, {"sla-r": road})


@pytest.fixture(autouse=True)
def _artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SlaMajorRoadStore, "load", staticmethod(lambda: (ROAD,)))
    monkeypatch.setattr(
        SlaOsmMajorRoadMappingStore, "load", staticmethod(lambda: mapping_with([entry(lon) for lon in LONGITUDES]))
    )


def run(provider: _BatchProvider, *, baseline: bool = True, config: DrivingConfig = BATCH_CONFIG, entries=None):  # type: ignore[no-untyped-def]
    mapping = mapping_with(entries if entries is not None else [entry(lon) for lon in LONGITUDES])
    return compute_major_road_access(*ORIGIN, provider, config, mapping=mapping, baseline=baseline)


class TestBatchWidening:
    def test_no_extra_batch_when_the_first_batch_validates(self) -> None:
        provider = _BatchProvider(good={LONGITUDES[1]})
        outcome = run(provider)
        assert outcome.result.status == ComponentStatus.CALCULATED
        assert len(provider.baseline_destinations) == BATCH
        assert outcome.result.value["rejection_summary"]["route_batches_used"] == 1

    def test_widens_to_the_next_batch_when_the_first_has_no_valid_entry(self) -> None:
        provider = _BatchProvider(good={LONGITUDES[4]})
        outcome = run(provider)
        assert outcome.result.status == ComponentStatus.CALCULATED
        assert len(provider.baseline_destinations) == 2 * BATCH
        assert outcome.result.value["rejection_summary"]["route_batches_used"] == 2
        assert (
            outcome.selected_point is not None
            and round(outcome.selected_point.routing_coordinate[1], 5) == LONGITUDES[4]
        )

    def test_stops_at_the_configured_number_of_batches(self) -> None:
        provider = _BatchProvider(good=set())
        outcome = run(provider)
        assert outcome.result.status == ComponentStatus.NOT_ASSESSED
        assert len(provider.baseline_destinations) == 3 * BATCH
        assert outcome.result.value["rejection_summary"]["route_batches_used"] == 3

    def test_paid_path_never_widens(self) -> None:
        provider = _BatchProvider(good={LONGITUDES[7]})  # only a far entry is valid
        outcome = run(provider, baseline=False)
        assert outcome.result.status == ComponentStatus.NOT_ASSESSED
        assert provider.baseline_destinations == []
        assert {round(d[1], 5) for d in provider.driving_destinations} <= set(LONGITUDES[:BATCH])


class TestFarTargetFilter:
    def test_target_far_from_its_road_is_never_routed(self) -> None:
        far = entry(103.8001, target_lat=1.30090)  # ~100 m north of the road, nearest to the origin
        provider = _BatchProvider(good={LONGITUDES[0]})
        outcome = run(provider, entries=[far, *[entry(lon) for lon in LONGITUDES]])
        assert (far.target_latitude, far.target_longitude) not in provider.baseline_destinations
        assert outcome.result.value["rejection_summary"]["catalogue_entries_dropped_target_far_from_road"] == 1

    def test_target_within_the_limit_is_kept(self) -> None:
        near = entry(103.8001, target_lat=1.30030)  # ~33 m from the road (limit is 50 m)
        provider = _BatchProvider(good=set())
        outcome = run(provider, entries=[near, *[entry(lon) for lon in LONGITUDES]])
        assert (near.target_latitude, near.target_longitude) in provider.baseline_destinations
        assert outcome.result.value["rejection_summary"]["catalogue_entries_dropped_target_far_from_road"] == 0


class TestStoredRejectionDiagnostics:
    def test_rejected_candidates_keep_their_geometry_diagnostics(self) -> None:
        outcome = run(_BatchProvider(good=set()))
        summary = outcome.result.value["rejection_summary"]
        assert summary["candidates_considered"] == 3 * BATCH
        assert summary["routed"] == 3 * BATCH and summary["validated"] == 0 and summary["rejected"] == 3 * BATCH
        assert summary["rejection_reasons"] == {"no_sustained_sla_major_road_overlap": 3 * BATCH}
        assert any("Rejection summary" in item for item in outcome.result.limitations)
        first = outcome.result.evidence[0]
        for key in (
            "reason",
            "catalogue_target_latitude",
            "routed_distance_metres",
            "sustained_overlap_metres",
            "trailing_overlap_metres",
            "route_end_shortfall_metres",
            "closest_approach_to_road_metres",
            "route_points",
        ):
            assert key in first

    def test_unrouted_candidates_are_recorded_with_their_reason(self) -> None:
        class NoRoute(_BatchProvider):
            def get_baseline_driving_route(self, origin, destination):  # type: ignore[no-untyped-def]
                raise RoutingUnavailableError("no route", self.provider_name)

        outcome = run(NoRoute(good=set()))
        assert outcome.result.status == ComponentStatus.NOT_ASSESSED
