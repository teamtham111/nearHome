"""Tests for the rebuilt Driving engine (Part 7 + Part 11 acceptance criteria)."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.adapters.routing.base import RouteMatrixResponse, RouteMode, RouteResult, RouteStep, RoutingProvider
from app.adapters.transport_data.road_access import RoadAccessPoint, RoadAccessPointStore
from app.domain.enums import ComponentStatus
from app.domain.transport_models import ComponentResult, build_rollup, not_assessed
from app.engines.driving.engine import compute_driving_model
from app.engines.driving.major_road_access import MajorRoadAccessOutcome, compute_major_road_access
from app.engines.driving.parking_convenience import compute_parking_convenience
from app.engines.driving.peak_access_penalty import compute_peak_access_penalty
from app.engines.driving.route_connectivity import compute_route_connectivity
from app.engines.transport_config import DrivingConfig
from app.tests.routing_helpers import DHOBY_GHAUT, TAMPINES, AlwaysFailingRoutingProvider, FixedDurationRoutingProvider

NEAR_POINT = RoadAccessPoint("Near But Slow Slip Road", "PIE", "eastbound", 1.30005, 103.80005)
FAR_POINT = RoadAccessPoint("Far But Fast Junction", "CTE", "northbound", 1.31000, 103.81500)


class _DirectionalDurationProvider(RoutingProvider):
    """Returns a duration keyed by destination name-recognisable coordinate,
    so a geographically closer point can be made deliberately slower than a
    farther one — proving selection uses routed duration, not distance."""

    provider_name = "DIRECTIONAL_STUB"

    def __init__(self, durations: dict[tuple[float, float], float]) -> None:
        self.durations = durations

    def _duration_for(self, destination: tuple[float, float]) -> float:
        for coords, minutes in self.durations.items():
            if abs(coords[0] - destination[0]) < 1e-4 and abs(coords[1] - destination[1]) < 1e-4:
                return minutes
        return 999.0

    def get_walking_route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        minutes = self._duration_for(destination)
        return RouteResult(minutes, round(minutes * 80), None, minutes, [], self.provider_name, None, None, False)

    def get_driving_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        departure_time: datetime,
        traffic_aware: bool = True,
    ) -> RouteResult:
        minutes = self._duration_for(destination)
        return RouteResult(
            minutes,
            round(minutes * 500),
            None,
            None,
            [RouteStep("Drive", "DRIVE", round(minutes * 500), minutes)],
            self.provider_name,
            departure_time,
            None,
            traffic_aware,
        )

    def get_driving_alternatives(
        self, origin: tuple[float, float], destination: tuple[float, float], departure_time: datetime
    ) -> list[RouteResult]:
        primary = self.get_driving_route(origin, destination, departure_time)
        return [primary]

    def get_transit_route(
        self, origin: tuple[float, float], destination: tuple[float, float], departure_time: datetime
    ) -> RouteResult:
        return self.get_driving_route(origin, destination, departure_time)

    def get_route_matrix(
        self,
        origins: list[tuple[float, float]],
        destinations: list[tuple[float, float]],
        mode: RouteMode,
        departure_time: datetime | None = None,
    ) -> RouteMatrixResponse:
        raise NotImplementedError


class TestMajorRoadAccessComponent:
    def test_selects_useful_not_geographically_closest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RoadAccessPointStore, "is_usable", classmethod(lambda cls: True))
        monkeypatch.setattr(RoadAccessPointStore, "nearby", classmethod(lambda cls, *a, **k: [NEAR_POINT, FAR_POINT]))
        provider = _DirectionalDurationProvider(
            {
                (NEAR_POINT.latitude, NEAR_POINT.longitude): 18.0,  # closer, but slow (traffic light heavy)
                (FAR_POINT.latitude, FAR_POINT.longitude): 6.0,  # farther, but genuinely faster
            }
        )
        outcome = compute_major_road_access(1.3000, 103.8000, provider)
        assert outcome.selected_point is not None
        assert outcome.selected_point.name == FAR_POINT.name

    def test_does_not_rely_on_five_hardcoded_anchors(self) -> None:
        points = RoadAccessPointStore.load()
        assert len(points) > 5
        expressways = {p.expressway for p in points}
        assert len(expressways) >= 5  # covers multiple real expressways, not one anchor set

    def test_no_haversine_used_as_driving_time(self) -> None:
        stub = FixedDurationRoutingProvider(minutes=7.3)
        outcome = compute_major_road_access(*DHOBY_GHAUT, stub)
        assert outcome.result.status == ComponentStatus.CALCULATED
        assert outcome.result.value["peak_duration_minutes"] == 7.3

    def test_provider_error_does_not_fabricate_a_route(self) -> None:
        failing = AlwaysFailingRoutingProvider()
        outcome = compute_major_road_access(*DHOBY_GHAUT, failing)
        assert outcome.result.status == ComponentStatus.PROVIDER_ERROR
        assert outcome.result.score is None

    def test_missing_dataset_is_not_assessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(RoadAccessPointStore, "is_usable", classmethod(lambda cls: False))
        stub = FixedDurationRoutingProvider()
        outcome = compute_major_road_access(*DHOBY_GHAUT, stub)
        assert outcome.result.status == ComponentStatus.NOT_ASSESSED


class TestPeakAccessPenalty:
    def test_uses_the_same_access_point_for_both_measurements(self) -> None:
        stub = FixedDurationRoutingProvider(minutes=5.0)
        outcome = compute_major_road_access(*DHOBY_GHAUT, stub)
        result = compute_peak_access_penalty(*DHOBY_GHAUT, stub, outcome)
        assert result.status == ComponentStatus.CALCULATED
        assert outcome.selected_point is not None
        assert result.evidence[0]["selected_access_point"] == outcome.selected_point.name

    def test_penalty_is_peak_minus_off_peak(self) -> None:
        class VariablePeakProvider(FixedDurationRoutingProvider):
            def get_driving_route(
                self,
                origin: tuple[float, float],
                destination: tuple[float, float],
                departure_time: datetime,
                traffic_aware: bool = True,
            ) -> RouteResult:
                minutes = 20.0 if 6 <= departure_time.hour < 9 else 12.0
                route = super().get_driving_route(origin, destination, departure_time, traffic_aware)
                route.duration_minutes = minutes
                return route

        provider = VariablePeakProvider()
        outcome = compute_major_road_access(*DHOBY_GHAUT, provider)
        result = compute_peak_access_penalty(*DHOBY_GHAUT, provider, outcome)
        assert result.value["penalty_minutes"] == 8.0

    def test_no_selected_access_point_is_not_assessed(self) -> None:
        empty_outcome = MajorRoadAccessOutcome(not_assessed("major_road_access", 0.35, "x"), None, None, [])
        stub = FixedDurationRoutingProvider()
        result = compute_peak_access_penalty(*DHOBY_GHAUT, stub, empty_outcome)
        assert result.status == ComponentStatus.NOT_ASSESSED


class TestRouteConnectivity:
    def test_high_overlap_alternative_does_not_count_as_independent(self) -> None:
        # Force alternatives that share the same road name -> substantially_overlapping.
        from app.tests.routing_helpers import FixedDurationRoutingProvider as _Fixed

        class SameRoadProvider(_Fixed):
            def get_driving_alternatives(
                self, origin: tuple[float, float], destination: tuple[float, float], departure_time: datetime
            ) -> list[RouteResult]:
                primary = self.get_driving_route(origin, destination, departure_time)
                primary.route_steps = [RouteStep("Continue on Pan Island Expressway (PIE)", "DRIVE", 5000, 10)]
                alt = self.get_driving_route(origin, destination, departure_time)
                alt.route_steps = [RouteStep("Stay on Pan Island Expressway (PIE)", "DRIVE", 5100, 11)]
                alt.is_alternative = True
                return [primary, alt]

        provider = SameRoadProvider(minutes=6.0)
        outcome = compute_major_road_access(*DHOBY_GHAUT, provider)
        result = compute_route_connectivity(*DHOBY_GHAUT, provider, outcome)
        assert result.status == ComponentStatus.CALCULATED
        assert result.value["independent_alternatives"] == 0

    def test_no_access_point_selected_is_not_assessed(self) -> None:
        stub = FixedDurationRoutingProvider()
        empty_outcome = MajorRoadAccessOutcome(not_assessed("major_road_access", 0.35, "x"), None, None, [])
        result = compute_route_connectivity(*DHOBY_GHAUT, stub, empty_outcome)
        assert result.status == ComponentStatus.NOT_ASSESSED


class TestParkingConvenience:
    def test_missing_carpark_data_is_not_assessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.parking.hdb_carpark import HdbCarparkStore

        monkeypatch.setattr(HdbCarparkStore, "is_usable", classmethod(lambda cls: False))
        stub = FixedDurationRoutingProvider()
        result = compute_parking_convenience(*DHOBY_GHAUT, stub)
        assert result.status == ComponentStatus.NOT_ASSESSED
        assert "not assessed" in " ".join(result.limitations).lower() or result.score is None

    def test_carpark_type_alone_does_not_determine_score(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two carparks of the *same* type at different walk distances must
        score differently — type can only ever be a small modifier."""
        from app.adapters.parking.hdb_carpark import HdbCarpark, HdbCarparkStore

        close_carpark = HdbCarpark("C1", "Close Carpark", 1.30001, 103.80001, "SURFACE CAR PARK", "", "", "", "", "")
        monkeypatch.setattr(HdbCarparkStore, "is_usable", classmethod(lambda cls: True))
        monkeypatch.setattr(HdbCarparkStore, "nearest", classmethod(lambda cls, *a, **k: (close_carpark, 20.0)))

        close_stub = FixedDurationRoutingProvider(minutes=1.5)
        far_stub = FixedDurationRoutingProvider(minutes=6.0)
        close_result = compute_parking_convenience(1.3000, 103.8000, close_stub)
        far_result = compute_parking_convenience(1.3000, 103.8000, far_stub)
        assert close_result.score is not None and far_result.score is not None
        assert close_result.score > far_result.score

    def test_walk_beyond_practical_cutoff_is_not_assessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.parking.hdb_carpark import HdbCarpark, HdbCarparkStore

        far_carpark = HdbCarpark("C2", "Far Carpark", 1.301, 103.801, "SURFACE CAR PARK", "", "", "", "", "")
        monkeypatch.setattr(HdbCarparkStore, "is_usable", classmethod(lambda cls: True))
        monkeypatch.setattr(HdbCarparkStore, "nearest", classmethod(lambda cls, *a, **k: (far_carpark, 350.0)))
        slow_stub = FixedDurationRoutingProvider(minutes=15.0)
        result = compute_parking_convenience(1.3000, 103.8000, slow_stub)
        assert result.status == ComponentStatus.NOT_ASSESSED


class TestDrivingRecommendationGating:
    def test_incomplete_coverage_excludes_overall_score(self) -> None:
        config = DrivingConfig()
        components = [
            ComponentResult(
                "major_road_access", {}, 90.0, config.weight_major_road_access, ComponentStatus.CALCULATED, "x"
            ),
            not_assessed("route_connectivity", config.weight_route_connectivity, "x"),
            not_assessed("peak_access_penalty", config.weight_peak_access_penalty, "x"),
            not_assessed("parking_convenience", config.weight_parking_convenience, "x"),
        ]
        rollup = build_rollup(components, config.min_core_weight_coverage)
        assert rollup.overall_score is None
        assert rollup.display_score == 90.0
        assert rollup.counts_toward_recommendation is False


class TestDrivingGoldenCases:
    def test_full_model_runs_end_to_end_without_error(self) -> None:
        stub = FixedDurationRoutingProvider(minutes=8.0)
        rollup = compute_driving_model(*TAMPINES, stub)
        assert len(rollup.components) == 4
        names = {c.name for c in rollup.components}
        assert names == {"major_road_access", "route_connectivity", "peak_access_penalty", "parking_convenience"}

    def test_provider_outage_leaves_every_component_not_assessed_or_error(self) -> None:
        failing = AlwaysFailingRoutingProvider()
        rollup = compute_driving_model(*TAMPINES, failing)
        assert rollup.overall_score is None
        assert rollup.display_score is None
        for component in rollup.components:
            assert component.status in (ComponentStatus.NOT_ASSESSED, ComponentStatus.PROVIDER_ERROR)

    def test_general_rollup_ignores_regular_destination_requests(self) -> None:
        stub = FixedDurationRoutingProvider(minutes=8.0)
        destination = [("Work", (1.3009, 103.8563), datetime.now())]
        without_destination = compute_driving_model(*TAMPINES, stub)
        with_destination = compute_driving_model(*TAMPINES, stub, destination_requests=destination)
        assert [c.name for c in with_destination.components] == [c.name for c in without_destination.components]
        assert with_destination.display_score == without_destination.display_score

    def test_four_component_weighted_formula(self) -> None:
        config = DrivingConfig()
        components = [
            ComponentResult(
                "major_road_access", {}, 80.0, config.weight_major_road_access, ComponentStatus.CALCULATED, "x"
            ),
            ComponentResult(
                "route_connectivity", {}, 90.0, config.weight_route_connectivity, ComponentStatus.CALCULATED, "x"
            ),
            ComponentResult(
                "peak_access_penalty", {}, 70.0, config.weight_peak_access_penalty, ComponentStatus.CALCULATED, "x"
            ),
            ComponentResult(
                "parking_convenience", {}, 60.0, config.weight_parking_convenience, ComponentStatus.CALCULATED, "x"
            ),
        ]
        rollup = build_rollup(components, config.min_core_weight_coverage)
        assert rollup.overall_score == pytest.approx(76.0)
        assert rollup.coverage_ratio == 1.0
        assert rollup.is_complete is True
