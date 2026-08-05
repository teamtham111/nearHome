"""Tests for the rebuilt Public Transport engine (Part 6 + Part 11 acceptance criteria)."""

from __future__ import annotations

import pytest

from app.adapters.routing.base import RouteResult, RouteStep
from app.adapters.transport_data.lta_bus import BusServiceInfo, FrequencyRange, LtaBusDataStore
from app.domain.enums import ComponentStatus
from app.domain.transport_models import ComponentResult, ModelRollup, build_rollup, not_assessed
from app.engines.public_transport.access import compute_access
from app.engines.public_transport.bus_coverage import compute_bus_coverage
from app.engines.public_transport.engine import compute_public_transport_model
from app.engines.public_transport.mrt_reach import compute_mrt_reach
from app.engines.public_transport.route_resilience import compute_route_resilience
from app.engines.transport_config import PublicTransportConfig
from app.networks.bus_network import BusNetwork, CorridorInfo
from app.networks.rail_graph import RailGraph
from app.tests.routing_helpers import (
    DHOBY_GHAUT,
    TAMPINES,
    WOODLANDS,
    YISHUN,
    AlwaysFailingRoutingProvider,
    FixedDurationRoutingProvider,
)


class TestAccessComponent:
    class _TwoStationGraph:
        is_loaded = True

        def nearby_station_codes(self, *_args, **_kwargs):
            return [("A1", 10.0), ("B1", 20.0)]

        def station_name_for_code(self, code):
            return {"A1": "Station A", "B1": "Station B"}.get(code)

        def station_by_code(self, code):
            from app.adapters.transport_data.rail_data import RailStation

            return {
                "A1": RailStation("Station A", ("A1",), ("AL",), False, 1.31, 103.81, True),
                "B1": RailStation("Station B", ("B1",), ("BL",), False, 1.32, 103.82, True),
            }.get(code)

    def test_access_scores_only_the_single_best_entry_path(self) -> None:
        from app.adapters.reference_data import ReferenceDataStore

        class VariableWalk(FixedDurationRoutingProvider):
            def get_walking_route(self, origin, destination):
                result = super().get_walking_route(origin, destination)
                result.duration_minutes = 3.0 if destination[0] < 1.315 else 18.0
                result.walking_minutes = result.duration_minutes
                return result

        # No bus data is needed for this direct-rail-only assertion.
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: []))
        try:
            result = compute_access(1.30, 103.80, VariableWalk(), rail_graph=self._TwoStationGraph())
        finally:
            monkeypatch.undo()
        assert result.value["selected_access_path"]["physical_station_id"] == "Station A"
        assert result.score == 88.0

    def test_frequent_direct_feeder_to_interchange_is_a_practical_rail_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.engines.public_transport.access as access_module
        from app.adapters.reference_data import BusStop, ReferenceDataStore
        from app.adapters.transport_data.rail_data import RailStation

        stop = BusStop("99999", "Feeder Stop", 1.3001, 103.8001, "Test Road", [])
        station = RailStation("Test Interchange", ("S1", "S2"), ("AL", "BL"), True, 1.32, 103.82, True)

        class OneStationGraph:
            is_loaded = True

            def nearby_station_codes(self, *_args, **_kwargs):
                return [("S1", 1000.0)]

            def station_name_for_code(self, code):
                return "Test Interchange" if code in {"S1", "S2"} else None

            def station_by_code(self, code):
                return station if code in {"S1", "S2"} else None

        class FeederRouting(FixedDurationRoutingProvider):
            def get_walking_route(self, origin, destination):
                result = super().get_walking_route(origin, destination)
                result.duration_minutes = 3.0 if destination == (1.3001, 103.8001) else 30.0
                result.walking_minutes = result.duration_minutes
                return result

            def get_transit_route(self, origin, destination, departure_time):
                return RouteResult(
                    duration_minutes=7.0,
                    distance_metres=1200,
                    transfers=0,
                    walking_minutes=1.0,
                    route_steps=[RouteStep("Take feeder 410W", "TRANSIT", 1000, 6.0, transit_service_number="410W")],
                    provider=self.provider_name,
                    departure_time=departure_time,
                    arrival_time=None,
                    traffic_aware=False,
                    transit_minutes=6.0,
                )

        class OneCorridorNetwork:
            def corridor_for(self, key):
                return "FEEDER-CORRIDOR"

        info = BusServiceInfo(
            "410W", 1, "SMRT", "FEEDER", "A", "B", "", {"AM_PEAK": FrequencyRange(8, 12, 10, "AM_PEAK")}
        )
        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: [stop]))
        monkeypatch.setattr(LtaBusDataStore, "is_usable", classmethod(lambda cls: True))
        monkeypatch.setattr(LtaBusDataStore, "services_by_stop", classmethod(lambda cls, code: {("410W", 1)}))
        monkeypatch.setattr(LtaBusDataStore, "service_info", classmethod(lambda cls, key: info))
        monkeypatch.setattr(access_module, "get_bus_network", lambda: OneCorridorNetwork())

        result = compute_access(1.3, 103.8, FeederRouting(), rail_graph=OneStationGraph())
        feeder = next(entry for entry in result.value["practical_rail_entries"] if entry["access_mode"] == "feeder_bus")
        assert feeder["physical_station_id"] == "Test Interchange"
        assert feeder["scheduled_wait_proxy_minutes"] == 5.0
        assert feeder["transfers_before_rail"] == 0

    def test_walking_time_comes_from_the_provider_not_from_distance(self) -> None:
        """Two destinations at very different distances must report the exact
        same walk_minutes when the provider always returns that value —
        proving the engine never computes distance / assumed_speed itself."""
        stub = FixedDurationRoutingProvider(minutes=4.2)
        result = compute_access(*DHOBY_GHAUT, stub)
        assert result.status == ComponentStatus.CALCULATED
        assert result.evidence
        for entry in result.evidence:
            assert entry["walk_minutes"] == 4.2

    def test_provider_error_does_not_fabricate_a_route(self) -> None:
        failing = AlwaysFailingRoutingProvider()
        result = compute_access(*DHOBY_GHAUT, failing)
        assert result.status == ComponentStatus.PROVIDER_ERROR
        assert result.score is None
        assert result.value is None

    def test_no_candidates_within_prefilter_is_a_genuine_low_result(self) -> None:
        stub = FixedDurationRoutingProvider()
        # Middle of the sea, far from any bus stop or MRT station.
        result = compute_access(1.05, 103.60, stub)
        assert result.status == ComponentStatus.CALCULATED
        assert result.score == PublicTransportConfig().access_score_floor

    def test_woodlands_uses_the_complete_northern_network(self) -> None:
        """Woodlands must find its own real MRT/LRT station, not silently
        fail because of an incomplete station fixture."""
        stub = FixedDurationRoutingProvider(minutes=3.0)
        result = compute_access(*WOODLANDS, stub)
        assert result.status == ComponentStatus.CALCULATED
        mrt_entries = [e for e in result.evidence if e["access_point_type"] == "mrt_station"]
        assert mrt_entries
        assert any(e["station_name"] == "Woodlands" for e in mrt_entries)

    def test_opposite_direction_stops_remain_separate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.reference_data import BusStop, ReferenceDataStore

        stop_a = BusStop("11111", "Opp Test Stop", 1.3000, 103.8000, "Test Road", [])
        stop_b = BusStop("22222", "Test Stop", 1.30001, 103.80001, "Test Road", [])
        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: [stop_a, stop_b]))
        stub = FixedDurationRoutingProvider(minutes=2.0)
        result = compute_access(1.3000, 103.8000, stub)
        bus_codes = {e["bus_stop_code"] for e in result.evidence if e["access_point_type"] == "bus_stop"}
        assert bus_codes == {"11111", "22222"}


class TestBusCoverageComponent:
    class _FakeBusNetwork(BusNetwork):
        def __init__(self, direct: int, one_transfer: int) -> None:
            super().__init__()
            self._direct = {f"C{i}" for i in range(direct)}
            self._one_transfer = {f"T{i}" for i in range(one_transfer)}

        def direct_corridors_for_stops(self, stop_codes: set[str]) -> set[str]:
            return self._direct

        def one_transfer_corridors(self, direct_stop_codes: set[str], direct_corridor_ids: set[str]) -> set[str]:
            return self._one_transfer

        def corridor_info(self, corridor_id: str) -> CorridorInfo | None:
            return CorridorInfo(corridor_id, frozenset({("1", 1)}), "Somewhere")

    def test_many_stops_one_corridor_does_not_outrank_few_stops_many_corridors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.adapters.transport_data.lta_bus import LtaBusDataStore

        monkeypatch.setattr(LtaBusDataStore, "is_usable", classmethod(lambda cls: True))

        ten_stops_one_corridor = compute_bus_coverage(
            [f"stop{i}" for i in range(10)], bus_network=self._FakeBusNetwork(direct=1, one_transfer=0)
        )
        four_stops_four_corridors = compute_bus_coverage(
            [f"stop{i}" for i in range(4)], bus_network=self._FakeBusNetwork(direct=4, one_transfer=0)
        )
        assert four_stops_four_corridors.score is not None
        assert ten_stops_one_corridor.score is not None
        assert four_stops_four_corridors.score > ten_stops_one_corridor.score

    def test_no_usable_stops_is_not_assessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.transport_data.lta_bus import LtaBusDataStore

        monkeypatch.setattr(LtaBusDataStore, "is_usable", classmethod(lambda cls: True))
        result = compute_bus_coverage([])
        assert result.status == ComponentStatus.NOT_ASSESSED

    def test_unusable_reference_data_is_not_assessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.transport_data.lta_bus import LtaBusDataStore

        monkeypatch.setattr(LtaBusDataStore, "is_usable", classmethod(lambda cls: False))
        result = compute_bus_coverage(["11111"])
        assert result.status == ComponentStatus.NOT_ASSESSED


class TestMrtReachComponent:
    def test_bishan_deduplicates_physical_stations_and_explains_additional_lines(self) -> None:
        result = compute_mrt_reach([], rail_graph=RailGraph(), origin_latitude=1.3521, origin_longitude=103.8498)
        evidence = result.evidence[0]
        assert result.value["primary_physical_station_id"] == "Bishan"
        assert evidence["direct_lines"] == ["CCL", "NSL"]
        assert evidence["additional_lines_with_one_transfer"] == []
        assert evidence["zero_transfer_30"] == 51
        assert evidence["one_transfer_30_incremental"] == 70
        assert len(evidence["reachable_station_summaries"]["zero_transfer"]) == 51
        assert len(evidence["reachable_station_summaries"]["one_transfer"]) == 70

    def test_reach_uses_geographically_closest_station_even_without_practical_access(self) -> None:
        graph = RailGraph()
        result = compute_mrt_reach(
            [],
            rail_graph=graph,
            origin_latitude=YISHUN[0],
            origin_longitude=YISHUN[1],
        )
        assert result.status == ComponentStatus.CALCULATED
        assert result.value["primary_physical_station_id"] == "Yishun"
        assert result.evidence[0]["selection_method"] == "geographic_nearest"
        assert result.evidence[0]["access_confirmed_practical_entry"] is False
        assert result.score != 15.0

    def test_reach_does_not_use_a_more_distant_access_station_as_its_origin(self) -> None:
        graph = RailGraph()
        result = compute_mrt_reach(
            [{"station_name": "Bishan", "codes": ["NS17", "CC15"], "walk_minutes": 1.0}],
            rail_graph=graph,
            origin_latitude=YISHUN[0],
            origin_longitude=YISHUN[1],
        )
        assert result.value["primary_physical_station_id"] == "Yishun"
        assert result.evidence[0]["alternative_practical_stations"][0]["physical_station_id"] == "Bishan"

    def test_does_not_only_count_lines_at_the_nearest_station(self) -> None:
        """A second, distinct walkable station and one-transfer reach must
        both influence the result — not just len(lines_at(nearest))."""
        graph = RailGraph()
        single_station = [
            {"station_name": "Yishun", "codes": ["NS13"], "lines": ["NSL"], "walk_minutes": 5.0},
        ]
        two_stations = [
            {"station_name": "Yishun", "codes": ["NS13"], "lines": ["NSL"], "walk_minutes": 5.0},
            {"station_name": "Khatib", "codes": ["NS14"], "lines": ["NSL"], "walk_minutes": 9.0},
        ]
        result_single = compute_mrt_reach(
            single_station, rail_graph=graph, origin_latitude=YISHUN[0], origin_longitude=YISHUN[1]
        )
        result_two = compute_mrt_reach(
            two_stations, rail_graph=graph, origin_latitude=YISHUN[0], origin_longitude=YISHUN[1]
        )
        assert result_single.score is not None and result_two.score is not None
        assert result_two.score == result_single.score
        assert result_two.evidence[0]["alternative_practical_stations"]

    def test_walking_alternative_does_not_increase_mrt_reach(self) -> None:
        graph = RailGraph()
        primary = [{"station_name": "Yishun", "codes": ["NS13"], "lines": ["NSL"], "walk_minutes": 5.0}]
        alternative = primary + [
            {"station_name": "Khatib", "codes": ["NS14"], "lines": ["NSL"], "walk_minutes": 6.0}
        ]
        assert compute_mrt_reach(
            alternative, rail_graph=graph, origin_latitude=YISHUN[0], origin_longitude=YISHUN[1]
        ).score == compute_mrt_reach(
            primary, rail_graph=graph, origin_latitude=YISHUN[0], origin_longitude=YISHUN[1]
        ).score

    def test_interchange_scores_higher_than_single_line_station(self) -> None:
        graph = RailGraph()
        interchange = [
            {"station_name": "Bishan", "codes": ["NS17", "CC15"], "lines": ["NSL", "CCL"], "walk_minutes": 4.0}
        ]
        single_line = [{"station_name": "Yishun", "codes": ["NS13"], "lines": ["NSL"], "walk_minutes": 4.0}]
        interchange_score = compute_mrt_reach(
            interchange, rail_graph=graph, origin_latitude=1.35101889777844, origin_longitude=103.850057208608
        ).score
        single_line_score = compute_mrt_reach(
            single_line, rail_graph=graph, origin_latitude=YISHUN[0], origin_longitude=YISHUN[1]
        ).score
        assert interchange_score is not None and single_line_score is not None
        assert interchange_score > single_line_score

    def test_no_practical_station_still_scores_the_nearest_rail_network(self) -> None:
        """Access reachability must not suppress the nearest-station MRT score."""
        graph = RailGraph()
        result = compute_mrt_reach(
            [], rail_graph=graph, origin_latitude=1.05, origin_longitude=103.60
        )
        assert result.status == ComponentStatus.CALCULATED
        assert result.score is not None
        assert result.value["primary_physical_station_id"] == "Tuas Crescent"
        assert result.evidence[0]["access_confirmed_practical_entry"] is False

    def test_missing_rail_graph_is_not_assessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = RailGraph()
        monkeypatch.setattr(RailGraph, "is_loaded", property(lambda self: False))
        result = compute_mrt_reach([], rail_graph=graph, origin_latitude=YISHUN[0], origin_longitude=YISHUN[1])
        assert result.status == ComponentStatus.NOT_ASSESSED
        assert result.score is None


class TestRouteResilienceComponent:
    def test_not_assessed_when_both_inputs_unavailable(self) -> None:
        bus = not_assessed("bus_coverage", 0.20, "no data")
        mrt = not_assessed("mrt_reach", 0.30, "no data")
        result = compute_route_resilience(bus, mrt)
        assert result.status == ComponentStatus.NOT_ASSESSED

    def test_more_independent_units_scores_higher(self) -> None:
        bus_low = ComponentResult(
            "bus_coverage", {"direct_corridors": 1}, 60.0, 0.20, ComponentStatus.CALCULATED, "x"
        )
        bus_high = ComponentResult(
            "bus_coverage", {"direct_corridors": 6}, 90.0, 0.20, ComponentStatus.CALCULATED, "x"
        )
        mrt = ComponentResult("mrt_reach", {"direct_lines": 1}, 70.0, 0.30, ComponentStatus.CALCULATED, "x")
        access_low = ComponentResult(
            "access",
            {"practical_rail_entries": [], "best_bus_entry": {"corridor_id": "one"}},
            60.0,
            0.30,
            ComponentStatus.CALCULATED,
            "x",
            evidence=[{"path_type": "walk_to_bus", "corridor_id": "one"}],
        )
        access_high = ComponentResult(
            "access",
            {
                "practical_rail_entries": [
                    {"physical_station_id": "A", "direct_lines": ["AL"]},
                    {"physical_station_id": "B", "direct_lines": ["BL"]},
                ],
                "primary_rail_entry": {"physical_station_id": "A", "direct_lines": ["AL"]},
                "best_bus_entry": {"corridor_id": "one"},
            },
            60.0,
            0.30,
            ComponentStatus.CALCULATED,
            "x",
            evidence=[
                {"path_type": "walk_to_bus", "corridor_id": "one"},
                {"path_type": "walk_to_bus", "corridor_id": "two"},
            ],
        )
        low = compute_route_resilience(bus_low, mrt, access_result=access_low)
        high = compute_route_resilience(bus_high, mrt, access_result=access_high)
        assert high.score is not None and low.score is not None
        assert high.score > low.score


class TestRecommendationGating:
    def test_incomplete_coverage_excludes_overall_score(self) -> None:
        config = PublicTransportConfig()
        components = [
            not_assessed("access", config.weight_access, "x"),
            not_assessed("bus_coverage", config.weight_bus_coverage, "x"),
            ComponentResult("mrt_reach", {}, 80.0, config.weight_mrt_reach, ComponentStatus.CALCULATED, "x"),
            not_assessed("route_resilience", config.weight_route_resilience, "x"),
        ]
        rollup = build_rollup(components, config.min_core_weight_coverage)
        # Only mrt_reach (weight 0.30) assessed — below the 0.6 minimum coverage.
        assert rollup.overall_score is None
        assert rollup.display_score == 80.0
        assert rollup.unrounded_score == 80.0
        assert rollup.counts_toward_recommendation is False
        assert rollup.is_complete is False

    def test_full_coverage_sets_overall_score(self) -> None:
        config = PublicTransportConfig()
        components = [
            ComponentResult("access", {}, 90.0, config.weight_access, ComponentStatus.CALCULATED, "x"),
            ComponentResult("bus_coverage", {}, 70.0, config.weight_bus_coverage, ComponentStatus.CALCULATED, "x"),
            ComponentResult("mrt_reach", {}, 85.0, config.weight_mrt_reach, ComponentStatus.CALCULATED, "x"),
            ComponentResult(
                "route_resilience", {}, 60.0, config.weight_route_resilience, ComponentStatus.CALCULATED, "x"
            ),
        ]
        rollup = build_rollup(components, config.min_core_weight_coverage)
        assert rollup.overall_score is not None
        assert rollup.unrounded_score == rollup.overall_score
        assert rollup.is_complete is True
        assert rollup.counts_toward_recommendation is True


class TestGoldenCases:
    """Sanity-check the full orchestrated model against real, geocoded
    coordinates using a deterministic mock routing provider. These prove the
    pipeline runs end-to-end without exceptions and produces evidence-backed,
    behaviourally-distinct results — not that specific score values are
    "correct" (there is no ground truth score to compare against)."""

    def _run(self, coords: tuple[float, float]) -> ModelRollup:
        stub = FixedDurationRoutingProvider(minutes=3.5)
        return compute_public_transport_model(*coords, stub)

    def test_dhoby_ghaut_has_broad_rail_reach(self) -> None:
        rollup = self._run(DHOBY_GHAUT)
        mrt_reach = next(c for c in rollup.components if c.name == "mrt_reach")
        assert mrt_reach.status == ComponentStatus.CALCULATED
        assert mrt_reach.value["direct_lines"] >= 3  # NSL/NEL/CCL interchange

    def test_yishun_has_narrower_reach_than_dhoby_ghaut(self) -> None:
        dhoby = self._run(DHOBY_GHAUT)
        yishun = self._run(YISHUN)
        dhoby_mrt = next(c for c in dhoby.components if c.name == "mrt_reach")
        yishun_mrt = next(c for c in yishun.components if c.name == "mrt_reach")
        assert dhoby_mrt.score is not None and yishun_mrt.score is not None
        assert dhoby_mrt.score > yishun_mrt.score

    def test_tampines_is_assessed_and_complete_enough_to_score(self) -> None:
        rollup = self._run(TAMPINES)
        assert rollup.display_score is not None
        assert len(rollup.assessed_component_names) >= 1

    def test_woodlands_does_not_crash_and_finds_its_own_station(self) -> None:
        rollup = self._run(WOODLANDS)
        access = next(c for c in rollup.components if c.name == "access")
        assert access.status == ComponentStatus.CALCULATED
        assert any(e.get("access_point_type") == "mrt_station" for e in access.evidence)

    def test_personal_journeys_remain_a_separate_concept(self) -> None:
        """The general model rollup never includes a personal-journey field
        — personal important-location journeys are stored separately
        (JourneyEstimate / journey_results), confirmed by the absence of any
        such key on the rollup's component dict."""
        rollup = self._run(DHOBY_GHAUT)
        for component in rollup.components:
            assert "personal_journey" not in component.to_dict()

    def test_frequency_is_not_a_standalone_scored_component(self) -> None:
        rollup = self._run(DHOBY_GHAUT)
        assert {component.name for component in rollup.components} == {
            "access",
            "bus_coverage",
            "mrt_reach",
            "route_resilience",
        }
