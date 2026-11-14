"""Tests for the rebuilt Public Transport engine (Part 6 + Part 11 acceptance criteria)."""

from __future__ import annotations

import pytest

from app.adapters.reference_data import haversine_m
from app.adapters.routing.base import RouteResult, RouteStep
from app.adapters.transport_data.lta_bus import BusRouteStop, BusServiceInfo, FrequencyRange, LtaBusDataStore
from app.domain.enums import ComponentStatus
from app.domain.transport_models import ComponentResult, ModelRollup, build_rollup, not_assessed
from app.engines.public_transport.access import _frequency_for_corridors, compute_access
from app.engines.public_transport.bus_coverage import compute_bus_coverage
from app.engines.public_transport.engine import compute_public_transport_model
from app.engines.public_transport.mrt_reach import compute_mrt_reach
from app.engines.public_transport.route_resilience import compute_route_resilience
from app.engines.transport_config import PublicTransportConfig
from app.networks.bus_network import BusNetwork, downstream_similarity
from app.networks.rail_graph import RailGraph
from app.tests.routing_helpers import (
    DHOBY_GHAUT,
    TAMPINES,
    WOODLANDS,
    YISHUN,
    AlwaysFailingRoutingProvider,
    FixedDurationRoutingProvider,
)


def _install_bus_data(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[tuple[str, int], list[str]],
    *,
    maximum_interval_minutes: float = 10.0,
) -> None:
    """Install a small direction-aware LTA network for bus tests."""
    route_rows = {
        key: [BusRouteStop(key[0], key[1], index + 1, stop_code, None) for index, stop_code in enumerate(stops)]
        for key, stops in routes.items()
    }
    by_stop: dict[str, set[tuple[str, int]]] = {}
    for key, stops in routes.items():
        for stop_code in stops:
            by_stop.setdefault(stop_code, set()).add(key)
    info = {
        key: BusServiceInfo(
            key[0],
            key[1],
            "TEST",
            "TRUNK",
            stops[0],
            stops[-1],
            "",
            {
                "AM_PEAK": FrequencyRange(
                    5,
                    maximum_interval_minutes,
                    (5 + maximum_interval_minutes) / 2,
                    "AM_PEAK",
                )
            },
        )
        for key, stops in routes.items()
    }
    monkeypatch.setattr(LtaBusDataStore, "is_usable", classmethod(lambda cls: True))
    monkeypatch.setattr(LtaBusDataStore, "services_by_stop", classmethod(lambda cls, code: by_stop.get(code, set())))
    monkeypatch.setattr(LtaBusDataStore, "route_stops", classmethod(lambda cls, key: route_rows.get(key, [])))
    monkeypatch.setattr(LtaBusDataStore, "service_info", classmethod(lambda cls, key: info.get(key)))


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

        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: [stop]))
        _install_bus_data(monkeypatch, {("410W", 1): ["99999", "90001"]})

        result = compute_access(1.3, 103.8, FeederRouting(), rail_graph=OneStationGraph())
        feeder = next(entry for entry in result.value["practical_rail_entries"] if entry["access_mode"] == "feeder_bus")
        assert feeder["physical_station_id"] == "Test Interchange"
        assert feeder["scheduled_wait_proxy_minutes"] == 3.8
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

    @pytest.mark.parametrize(
        ("maximum_interval", "eligible"),
        [(15.0, True), (15.01, False), (20.0, False)],
    )
    def test_bus_corridor_frequency_uses_fifteen_minute_combined_maximum_ceiling(
        self, monkeypatch: pytest.MonkeyPatch, maximum_interval: float, eligible: bool
    ) -> None:
        # One service is the simplest harmonic-rate case: its own maximum is
        # the combined maximum interval supplied to the eligibility check.
        _install_bus_data(
            monkeypatch,
            {("A", 1): ["S", "D"]},
            maximum_interval_minutes=maximum_interval,
        )
        corridors = _frequency_for_corridors("S", PublicTransportConfig(), BusNetwork())
        assert bool(corridors) is eligible

    def test_provider_error_does_not_fabricate_a_route(self) -> None:
        failing = AlwaysFailingRoutingProvider()
        result = compute_access(*DHOBY_GHAUT, failing)
        assert result.status == ComponentStatus.PROVIDER_ERROR
        assert result.score is None
        assert (result.value or {}).get("selected_access_path") is None
        assert result.value["routing_failed_requests"] > 0

    def test_partly_failed_routing_is_partial_and_not_rankable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.reference_data import ReferenceDataStore
        from app.adapters.routing.base import RoutingProviderError
        from app.engines.location_benchmarks.measurements import extract_measurements

        class StationBFails(FixedDurationRoutingProvider):
            def get_walking_route(
                self, origin: tuple[float, float], destination: tuple[float, float]
            ) -> RouteResult:
                if destination[0] > 1.315:
                    raise RoutingProviderError("boom", "FIXED_STUB")
                return super().get_walking_route(origin, destination)

        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: []))
        result = compute_access(
            1.30, 103.80, StationBFails(minutes=3.0), rail_graph=self._TwoStationGraph(), strict_incomplete=True  # type: ignore[arg-type]
        )
        assert result.status == ComponentStatus.PARTIALLY_ASSESSED
        assert any("failed" in limitation for limitation in result.limitations)
        rollup = {"components": [{"name": "access", "status": result.status.value, "score": result.score,
                                  "value": result.value}]}
        measured = extract_measurements("public_transport", rollup)["pt_access_cost_minutes"]
        assert measured["status"] == "unavailable"

    def test_implausible_walking_detour_is_not_used_or_invented(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.reference_data import ReferenceDataStore

        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: []))
        graph = self._TwoStationGraph()
        # Station A is ~1.5 km away in a straight line: 12,000 m is a >3x detour for both stations.
        detour = compute_access(
            1.30,
            103.80,
            FixedDurationRoutingProvider(minutes=3.0, distance_metres=12000),
            rail_graph=graph,  # type: ignore[arg-type]
            strict_incomplete=True,
        )
        assert detour.status == ComponentStatus.PARTIALLY_ASSESSED
        assert detour.value["walking_detours"]
        assert detour.value["selected_access_path"] is None
        assert any("implausibly long" in item for item in detour.limitations)
        # A plausible street detour (2x) and short walks are untouched.
        normal = compute_access(
            1.30, 103.80, FixedDurationRoutingProvider(minutes=3.0, distance_metres=3000), rail_graph=graph  # type: ignore[arg-type]
        )
        assert normal.status == ComponentStatus.CALCULATED
        assert normal.value["walking_detours"] == []

    def _one_flagged_walk_routing(self):
        class OneDetour(FixedDurationRoutingProvider):
            def __init__(self) -> None:
                super().__init__(minutes=3.0, distance_metres=1500)
                self.first: tuple[float, float] | None = None

            def get_walking_route(self, origin, destination):  # type: ignore[no-untyped-def]
                self.first = self.first or destination
                if destination == self.first:
                    self.distance_metres = 12000
                else:
                    self.distance_metres = 1500
                return super().get_walking_route(origin, destination)

        return OneDetour()

    def test_walking_detours_are_counted_apart_from_failed_requests(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.reference_data import ReferenceDataStore

        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: []))
        result = compute_access(
            1.30, 103.80, self._one_flagged_walk_routing(), rail_graph=self._TwoStationGraph(),  # type: ignore[arg-type]
            strict_incomplete=True,
        )
        assert len(result.value["walking_detours"]) == 1
        assert result.value["routing_failed_requests"] == 0
        assert result.value["routing_incomplete_requests"] == 1
        assert not any("routing request(s) failed" in item for item in result.limitations)
        assert any("implausibly long" in item for item in result.limitations)

    def test_default_policy_unranks_the_block_for_a_flagged_detour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.reference_data import ReferenceDataStore

        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: []))
        assert PublicTransportConfig().walking_detour_policy == "unrank_block"
        result = compute_access(
            1.30, 103.80, self._one_flagged_walk_routing(), rail_graph=self._TwoStationGraph(),  # type: ignore[arg-type]
            strict_incomplete=True,
        )
        assert result.status == ComponentStatus.PARTIALLY_ASSESSED

    def test_drop_stop_policy_keeps_the_block_complete_without_the_flagged_stop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.adapters.reference_data import ReferenceDataStore

        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: []))
        result = compute_access(
            1.30, 103.80, self._one_flagged_walk_routing(), PublicTransportConfig(walking_detour_policy="drop_stop"),
            rail_graph=self._TwoStationGraph(),  # type: ignore[arg-type]
            strict_incomplete=True,
        )
        assert result.status == ComponentStatus.CALCULATED
        assert len(result.value["walking_detours"]) == 1
        assert result.value["routing_incomplete_requests"] == 0
        assert result.value["selected_access_path"] is not None

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

    @pytest.mark.parametrize(
        ("minutes", "expected"), [(9.9, True), (10.0, True), (10.1, False)]
    )
    def test_bus_stop_access_uses_ten_minute_routed_walk_cutoff(
        self, monkeypatch: pytest.MonkeyPatch, minutes: float, expected: bool
    ) -> None:
        from app.adapters.reference_data import BusStop, ReferenceDataStore

        class EmptyRailGraph:
            def nearby_station_codes(self, *_args, **_kwargs):
                return []

        class RoutedWalk(FixedDurationRoutingProvider):
            def get_walking_route(self, origin, destination):
                result = super().get_walking_route(origin, destination)
                result.duration_minutes = minutes
                result.walking_minutes = minutes
                result.distance_metres = 300
                return result

        stop = BusStop("S", "Test stop", 1.3001, 103.8001, "Road", [])
        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: [stop]))
        _install_bus_data(monkeypatch, {("A", 1): ["S", "D"]})
        result = compute_access(1.3, 103.8, RoutedWalk(), rail_graph=EmptyRailGraph())
        assert ("S" in result.value["walkable_bus_stop_codes"]) is expected


class TestBusCoverageComponent:
    @pytest.mark.parametrize(("distance", "expected"), [(399.0, True), (400.0, True), (401.0, False)])
    def test_bus_coverage_uses_four_hundred_metre_routed_distance_cutoff(
        self, monkeypatch: pytest.MonkeyPatch, distance: float, expected: bool
    ) -> None:
        _install_bus_data(monkeypatch, {("A", 1): ["S", "D"]})
        result = compute_bus_coverage([{"bus_stop_code": "S", "walk_distance_metres": distance}])
        assert (result.status == ComponentStatus.CALCULATED) is expected

    def test_access_can_include_stop_that_bus_coverage_excludes_by_distance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.adapters.reference_data import BusStop, ReferenceDataStore

        class EmptyRailGraph:
            def nearby_station_codes(self, *_args, **_kwargs):
                return []

        class RoutedWalk(FixedDurationRoutingProvider):
            def get_walking_route(self, origin, destination):
                result = super().get_walking_route(origin, destination)
                result.duration_minutes = 8.0
                result.walking_minutes = 8.0
                result.distance_metres = 650
                return result

        stop = BusStop("S", "Test stop", 1.3025, 103.8, "Road", [])
        monkeypatch.setattr(ReferenceDataStore, "bus_stops", classmethod(lambda cls: [stop]))
        _install_bus_data(monkeypatch, {("A", 1): ["S", "D"]})
        access = compute_access(1.3, 103.8, RoutedWalk(), rail_graph=EmptyRailGraph())
        coverage = compute_bus_coverage(access.value["walkable_bus_stops"])
        assert access.value["walkable_bus_stop_codes"] == ["S"]
        assert coverage.status == ComponentStatus.NOT_ASSESSED

    def test_no_usable_stops_is_not_assessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.transport_data.lta_bus import LtaBusDataStore

        monkeypatch.setattr(LtaBusDataStore, "is_usable", classmethod(lambda cls: True))
        result = compute_bus_coverage([])
        assert result.status == ComponentStatus.NOT_ASSESSED

    def test_unusable_reference_data_is_not_assessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.adapters.transport_data.lta_bus import LtaBusDataStore

        monkeypatch.setattr(LtaBusDataStore, "is_usable", classmethod(lambda cls: False))
        result = compute_bus_coverage([{"bus_stop_code": "11111", "walk_distance_metres": 100.0}])
        assert result.status == ComponentStatus.NOT_ASSESSED


class TestBoardingStopSpecificCorridors:
    def test_upstream_overlap_does_not_merge_downstream_choices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_bus_data(
            monkeypatch,
            {("A", 1): ["1", "2", "3", "S", "4", "5", "6"], ("B", 1): ["1", "2", "3", "S", "7", "8", "9"]},
        )
        context = BusNetwork().corridors_for_boarding_stops({"S"})
        assert len(context.direct_corridor_ids()) == 2

    def test_services_terminating_at_the_boarding_stop_do_not_merge_corridors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Interchange pattern: two services start at S in different directions
        # and a third terminates at S. The terminating service's one-stop
        # sequence must not bridge the two distinct corridors.
        _install_bus_data(
            monkeypatch,
            {
                ("A", 1): ["S", "1", "2", "3", "4"],
                ("B", 1): ["S", "7", "8", "9", "10"],
                ("T", 1): ["X", "Y", "S"],
            },
        )
        context = BusNetwork().corridors_for_boarding_stops({"S"})
        assert len(context.direct_corridor_ids()) == 2
        terminating = BusNetwork.downstream_option(("T", 1), "S")
        assert terminating is not None
        assert context.corridor_for_option(terminating) is None

    def test_terminating_service_is_not_a_direct_option_and_does_not_discard_transfers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # T and C both terminate at S (one-stop downstream sequences). C can be
        # boarded at D1, downstream of B. Compared against C's own one-stop
        # sequence at S, the transfer option [D1, S] used to look like a
        # perfect overlap and was wrongly discarded as a duplicate.
        _install_bus_data(
            monkeypatch,
            {
                ("B", 1): ["S", "D1", "D2"],
                ("T", 1): ["X", "Y", "S"],
                ("C", 1): ["D1", "S"],
            },
        )
        context = BusNetwork().corridors_for_boarding_stops({"S"})
        terminating = BusNetwork.downstream_option(("T", 1), "S")
        assert terminating is not None
        assert terminating not in context._direct_options
        assert len(context.direct_corridor_ids()) == 1
        assert len(context.one_transfer_corridor_ids()) == 1
        transfer_services = {
            key
            for corridor_id in context.one_transfer_corridor_ids()
            for key in context.corridor_info(corridor_id).member_services  # type: ignore[union-attr]
        }
        assert ("C", 1) in transfer_services

    def test_a_terminating_service_is_not_a_source_of_transfer_options(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_bus_data(
            monkeypatch,
            {("T", 1): ["S"], ("U", 1): ["S", "Z"], ("V", 1): ["Z", "W", "V1"]},
        )
        context = BusNetwork().corridors_for_boarding_stops({"S"})
        assert len(context.direct_corridor_ids()) == 1
        assert len(context.one_transfer_corridor_ids()) == 1

    def test_reversed_order_is_not_a_shared_corridor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_bus_data(
            monkeypatch,
            {("A", 1): ["S", "1", "2", "3", "4", "5"], ("B", 1): ["S", "5", "4", "3", "2", "1"]},
        )
        network = BusNetwork()
        context = network.corridors_for_boarding_stops({"S"})
        options = [network.downstream_option(key, "S") for key in [("A", 1), ("B", 1)]]
        assert options[0] is not None and options[1] is not None
        assert downstream_similarity(options[0], options[1]) < 0.70
        assert len(context.direct_corridor_ids()) == 2

    def test_ordered_overlap_is_measured_and_config_controls_grouping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_bus_data(
            monkeypatch,
            {("A", 1): ["S", "1", "2", "3", "4", "5"], ("B", 1): ["S", "1", "2", "3", "8", "9"]},
        )
        default_context = BusNetwork(overlap_threshold=0.70).corridors_for_boarding_stops({"S"})
        changed_context = BusNetwork(overlap_threshold=0.60).corridors_for_boarding_stops({"S"})
        assert len(default_context.direct_corridor_ids()) == 2
        assert len(changed_context.direct_corridor_ids()) == 1
        stops = [{"bus_stop_code": "S", "walk_distance_metres": 100.0}]
        default_result = compute_bus_coverage(stops, config=PublicTransportConfig(corridor_overlap_threshold=0.70))
        changed_result = compute_bus_coverage(stops, config=PublicTransportConfig(corridor_overlap_threshold=0.60))
        assert default_result.value["direct_corridors"] == 2
        assert changed_result.value["direct_corridors"] == 1

    def test_opposite_directions_with_reversed_stops_remain_distinct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_bus_data(monkeypatch, {("99", 1): ["S", "1", "2"], ("99", 2): ["S", "2", "1"]})
        context = BusNetwork().corridors_for_boarding_stops({"S"})
        assert len(context.direct_corridor_ids()) == 2

    def test_transfer_search_does_not_use_stops_before_boarding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_bus_data(
            monkeypatch,
            {
                ("A", 1): ["UPSTREAM", "S", "DOWNSTREAM"],
                ("B", 1): ["UPSTREAM", "OTHER"],
            },
        )
        context = BusNetwork().corridors_for_boarding_stops({"S"})
        assert len(context.direct_corridor_ids()) == 1
        assert context.one_transfer_corridor_ids() == set()

    def test_transfer_search_uses_a_downstream_stop_after_boarding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_bus_data(
            monkeypatch,
            {
                ("A", 1): ["S", "TRANSFER"],
                ("B", 1): ["TRANSFER", "NEW_DESTINATION"],
            },
        )
        context = BusNetwork().corridors_for_boarding_stops({"S"})
        assert len(context.direct_corridor_ids()) == 1
        assert len(context.one_transfer_corridor_ids()) == 1

    def test_transfer_search_does_not_connect_distinct_nearby_stop_codes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_bus_data(
            monkeypatch,
            {
                ("A", 1): ["S", "ALIGHT_A"],
                ("B", 1): ["BOARD_B", "NEW_DESTINATION"],
            },
        )
        context = BusNetwork().corridors_for_boarding_stops({"S"})
        assert len(context.direct_corridor_ids()) == 1
        assert context.one_transfer_corridor_ids() == set()

    def test_same_service_from_two_nearby_boarding_stops_is_one_corridor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_bus_data(monkeypatch, {("A", 1): ["S1", "S2", "1", "2"]})
        context = BusNetwork().corridors_for_boarding_stops({"S1", "S2"})
        assert len(context.direct_corridor_ids()) == 1


class TestMrtReachComponent:
    def test_access_primary_station_wins_an_equal_cost_tie(self) -> None:
        result = compute_mrt_reach(
            [
                {
                    "physical_station_id": "Bencoolen",
                    "station_codes": ["DT21"],
                    "generalised_access_cost": 6.4,
                    "total_expected_minutes": 5.5,
                },
                {
                    "physical_station_id": "Dhoby Ghaut",
                    "station_codes": ["NS24", "NE6", "CC1"],
                    "generalised_access_cost": 6.4,
                    "total_expected_minutes": 5.5,
                    "is_primary_rail_entry": True,
                },
            ],
            rail_graph=RailGraph(),
        )

        assert result.value["primary_physical_station_id"] == "Dhoby Ghaut"
        assert result.evidence[0]["direct_lines"] == ["CCL", "NEL", "NSL"]

    def test_bishan_deduplicates_physical_stations_and_explains_additional_lines(self) -> None:
        result = compute_mrt_reach(
            [{"station_name": "Bishan", "codes": ["NS17", "CC15"], "walk_minutes": 4.0}],
            rail_graph=RailGraph(),
        )
        evidence = result.evidence[0]
        assert result.value["primary_physical_station_id"] == "Bishan"
        assert evidence["direct_lines"] == ["CCL", "NSL"]
        assert evidence["additional_lines_with_one_transfer"] == []
        assert evidence["zero_transfer_30"] == 51
        assert evidence["one_transfer_30_incremental"] == 70
        assert len(evidence["reachable_station_summaries"]["zero_transfer"]) == 51
        assert len(evidence["reachable_station_summaries"]["one_transfer"]) == 70

    def test_routed_best_station_beats_geographically_nearer_station(self) -> None:
        graph = RailGraph()
        yishun_station = graph.station_by_name("Yishun")
        bishan_station = graph.station_by_name("Bishan")
        assert yishun_station is not None and bishan_station is not None
        assert yishun_station.latitude is not None and yishun_station.longitude is not None
        assert bishan_station.latitude is not None and bishan_station.longitude is not None
        assert haversine_m(*YISHUN, yishun_station.latitude, yishun_station.longitude) < haversine_m(
            *YISHUN, bishan_station.latitude, bishan_station.longitude
        )
        result = compute_mrt_reach(
            [
                {
                    "physical_station_id": "Yishun",
                    "station_codes": ["NS13"],
                    "generalised_access_cost": 11.0,
                    "total_expected_minutes": 11.0,
                },
                {
                    "physical_station_id": "Bishan",
                    "station_codes": ["NS17", "CC15"],
                    "generalised_access_cost": 7.0,
                    "total_expected_minutes": 7.0,
                },
            ],
            rail_graph=graph,
        )
        assert result.status == ComponentStatus.CALCULATED
        assert result.value["primary_physical_station_id"] == "Bishan"
        assert result.evidence[0]["selection_method"] == "lowest_generalised_access_cost"
        assert result.evidence[0]["primary_generalised_access_cost"] == 7.0

    def test_selected_access_station_controls_the_graph_result(self) -> None:
        graph = RailGraph()
        bishan = compute_mrt_reach(
            [{"station_name": "Bishan", "codes": ["NS17", "CC15"], "walk_minutes": 4.0}], rail_graph=graph
        )
        yishun = compute_mrt_reach(
            [{"station_name": "Yishun", "codes": ["NS13"], "walk_minutes": 4.0}], rail_graph=graph
        )
        assert bishan.value["primary_physical_station_id"] == "Bishan"
        assert bishan.evidence[0]["direct_lines"] == ["CCL", "NSL"]
        assert bishan.score is not None and yishun.score is not None
        assert bishan.score > yishun.score

    def test_alternative_access_entries_do_not_change_selected_station_reach(self) -> None:
        """Only the lowest-cost qualifying Access station is the graph origin."""
        graph = RailGraph()
        single_station = [
            {"station_name": "Yishun", "codes": ["NS13"], "lines": ["NSL"], "walk_minutes": 5.0},
        ]
        two_stations = [
            {"station_name": "Yishun", "codes": ["NS13"], "lines": ["NSL"], "walk_minutes": 5.0},
            {"station_name": "Khatib", "codes": ["NS14"], "lines": ["NSL"], "walk_minutes": 9.0},
        ]
        result_single = compute_mrt_reach(
            single_station, rail_graph=graph
        )
        result_two = compute_mrt_reach(
            two_stations, rail_graph=graph
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
            alternative, rail_graph=graph
        ).score == compute_mrt_reach(
            primary, rail_graph=graph
        ).score

    def test_interchange_scores_higher_than_single_line_station(self) -> None:
        graph = RailGraph()
        interchange = [
            {"station_name": "Bishan", "codes": ["NS17", "CC15"], "lines": ["NSL", "CCL"], "walk_minutes": 4.0}
        ]
        single_line = [{"station_name": "Yishun", "codes": ["NS13"], "lines": ["NSL"], "walk_minutes": 4.0}]
        interchange_score = compute_mrt_reach(
            interchange, rail_graph=graph
        ).score
        single_line_score = compute_mrt_reach(
            single_line, rail_graph=graph
        ).score
        assert interchange_score is not None and single_line_score is not None
        assert interchange_score > single_line_score

    def test_no_practical_station_is_not_assessed(self) -> None:
        result = compute_mrt_reach([], rail_graph=RailGraph())
        assert result.status == ComponentStatus.NOT_ASSESSED
        assert result.score is None

    def test_home_to_station_access_is_not_added_to_structural_rail_minutes(self) -> None:
        graph = RailGraph()
        short_walk = compute_mrt_reach(
            [{"station_name": "Yishun", "codes": ["NS13"], "walk_minutes": 3.0}], rail_graph=graph
        )
        long_walk = compute_mrt_reach(
            [{"station_name": "Yishun", "codes": ["NS13"], "walk_minutes": 19.0}], rail_graph=graph
        )
        assert short_walk.score == long_walk.score
        assert short_walk.value["zero_transfer_30"] == long_walk.value["zero_transfer_30"]

    def test_missing_rail_graph_is_not_assessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = RailGraph()
        monkeypatch.setattr(RailGraph, "is_loaded", property(lambda self: False))
        result = compute_mrt_reach([], rail_graph=graph)
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


    @pytest.mark.parametrize("status", [ComponentStatus.PROVIDER_ERROR, ComponentStatus.PARTIALLY_ASSESSED])
    def test_failed_or_partial_access_is_unavailable_not_zero_fallbacks(self, status: ComponentStatus) -> None:
        bus = ComponentResult("bus_coverage", {"direct_corridors": 0}, 10.0, 0.20, ComponentStatus.CALCULATED, "x")
        mrt = ComponentResult("mrt_reach", {}, 10.0, 0.30, ComponentStatus.CALCULATED, "x")
        access = ComponentResult("access", None, None, 0.30, status, "failed")
        result = compute_route_resilience(bus, mrt, access_result=access)
        assert result.status == ComponentStatus.NOT_ASSESSED
        assert result.score is None
        assert result.value is None


class _FlakyWalkRouting(FixedDurationRoutingProvider):
    """Walking requests fail: the first ``fail_first`` attempts per destination
    (transient) or every attempt for destinations in ``always_fail`` (persistent)."""

    def __init__(self, *, fail_first: int = 0, always_fail_every: int = 0) -> None:
        super().__init__(minutes=4.0)
        self.fail_first = fail_first
        self.always_fail_every = always_fail_every
        self.attempts: dict[tuple[float, float], int] = {}

    def get_walking_route(self, origin: tuple[float, float], destination: tuple[float, float]) -> RouteResult:
        from app.adapters.routing.base import RoutingProviderError

        self.attempts[destination] = self.attempts.get(destination, 0) + 1
        if self.attempts[destination] <= self.fail_first:
            raise RoutingProviderError("transient", "FIXED_STUB", retryable=True)
        if self.always_fail_every and round(destination[0] * 1_000_000) % self.always_fail_every == 0:
            raise RoutingProviderError("persistent", "FIXED_STUB", retryable=True)
        return super().get_walking_route(origin, destination)


class TestIncompleteRoutingPolicy:
    def test_failed_requests_are_retried_once_before_counting(self) -> None:
        routing = _FlakyWalkRouting(fail_first=1)
        result = compute_access(*DHOBY_GHAUT, routing)
        assert result.status == ComponentStatus.CALCULATED
        assert result.value["routing_failed_requests"] == 0
        assert all(count == 2 for count in routing.attempts.values())

    def test_a_second_failure_is_not_retried_again(self) -> None:
        routing = _FlakyWalkRouting(fail_first=5)
        compute_access(*DHOBY_GHAUT, routing, strict_incomplete=True)
        assert all(count == 2 for count in routing.attempts.values())

    def _remaining_failure_routing(self) -> _FlakyWalkRouting:
        # Fails a deterministic subset of destinations on every attempt.
        for modulus in (2, 3, 4, 5, 7):
            routing = _FlakyWalkRouting(always_fail_every=modulus)
            access = compute_access(*DHOBY_GHAUT, routing, strict_incomplete=True)
            if access.status == ComponentStatus.PARTIALLY_ASSESSED:
                return _FlakyWalkRouting(always_fail_every=modulus)
        raise AssertionError("no partial-failure scenario found")

    def test_benchmark_path_does_not_publish_or_rank_incomplete_bus_and_rail_measures(self) -> None:
        rollup = compute_public_transport_model(
            *DHOBY_GHAUT, self._remaining_failure_routing(), strict_incomplete=True
        )
        by_name = {c.name: c for c in rollup.components}
        assert by_name["access"].status == ComponentStatus.PARTIALLY_ASSESSED
        for name in ("bus_coverage", "mrt_reach", "route_resilience"):
            assert by_name[name].status == ComponentStatus.NOT_ASSESSED
            assert by_name[name].score is None
        assert any("failed" in item or "incomplete" in item for item in by_name["bus_coverage"].limitations)
        assert rollup.counts_toward_recommendation is False
        assert rollup.overall_score is None

    def test_live_path_still_scores_but_flags_incomplete_routing_data(self) -> None:
        rollup = compute_public_transport_model(*DHOBY_GHAUT, self._remaining_failure_routing())
        by_name = {c.name: c for c in rollup.components}
        assert by_name["access"].status == ComponentStatus.CALCULATED
        assert by_name["access"].is_assessed
        assert by_name["route_resilience"].is_assessed
        assert rollup.counts_toward_recommendation is True
        assert rollup.overall_score is not None
        assert rollup.incomplete_routing is True
        for name in ("access", "bus_coverage", "mrt_reach", "route_resilience"):
            assert any("incomplete routing data" in item.lower() for item in by_name[name].limitations)
        assert any("incomplete routing data" in warning.lower() for warning in rollup.warnings)

    def test_complete_live_result_carries_no_incomplete_flag(self) -> None:
        rollup = compute_public_transport_model(*DHOBY_GHAUT, FixedDurationRoutingProvider(minutes=4.0))
        assert rollup.incomplete_routing is False

    def test_total_failure_stays_a_failure_in_both_modes(self) -> None:
        for strict in (False, True):
            rollup = compute_public_transport_model(
                *DHOBY_GHAUT, AlwaysFailingRoutingProvider(), strict_incomplete=strict
            )
            by_name = {c.name: c for c in rollup.components}
            assert by_name["access"].status == ComponentStatus.PROVIDER_ERROR
            assert by_name["route_resilience"].status == ComponentStatus.NOT_ASSESSED
            assert by_name["route_resilience"].value is None
            assert rollup.counts_toward_recommendation is False

    def test_incomplete_live_payload_is_not_stored_as_a_block_feature(self) -> None:
        from app.services.block_features import BlockFeatureService

        class Repo:
            def get_feature(self, *_args: object) -> None:
                return None

            def save_feature(self, **_kwargs: object) -> None:
                raise AssertionError("an incomplete live result must not be stored")

        outcome = BlockFeatureService(Repo()).get_or_compute(  # type: ignore[arg-type]
            "k", "public_transport", (1.3, 103.8), "t", lambda *_: {"incomplete_routing": True}
        )
        assert outcome.reused is False and outcome.payload["incomplete_routing"] is True


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
        evidence = mrt_reach.evidence[0]
        # MRT Reach must retain Access's selected physical station. Dhoby
        # Ghaut is one physical interchange represented by NS24/NE6/CC1.
        assert mrt_reach.value["primary_physical_station_id"] == "Dhoby Ghaut"
        assert mrt_reach.value["is_interchange"] is True
        assert set(evidence["direct_lines"]) == {"NSL", "NEL", "CCL"}

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


def _reference_lcs_length(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    """Straightforward O(n * m) table, kept as the oracle for the fast LCS."""
    previous = [0] * (len(right) + 1)
    for left_stop in left:
        current = [0]
        for index, right_stop in enumerate(right, 1):
            current.append(previous[index - 1] + 1 if left_stop == right_stop else max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def test_fast_lcs_matches_reference_including_repeated_stops() -> None:
    import random

    from app.networks.bus_network import _longest_common_subsequence_length

    rng = random.Random(20261011)
    for _ in range(2_000):
        # A small alphabet forces repeated stops, as on loop services.
        alphabet = [str(code) for code in range(rng.randint(1, 12))]
        left = tuple(rng.choice(alphabet) for _ in range(rng.randint(0, 25)))
        right = tuple(rng.choice(alphabet) for _ in range(rng.randint(0, 25)))
        assert _longest_common_subsequence_length(left, right) == _reference_lcs_length(left, right)


def test_overlap_checker_matches_similarity_threshold() -> None:
    import random

    from app.networks.bus_network import BoardingServiceOption, _OverlapChecker

    rng = random.Random(7)
    for threshold in (0.0, 0.3, 0.5, 0.7, 1.0):
        checker = _OverlapChecker(threshold)
        options = []
        for index in range(60):
            alphabet = [str(code) for code in range(rng.choice([3, 8, 40]))]
            length = rng.randint(0, 20)
            stops = tuple(rng.choice(alphabet) for _ in range(length))
            if rng.random() < 0.5:
                stops = tuple(dict.fromkeys(stops))  # unique-stop route
            options.append(BoardingServiceOption((str(index), 1), stops[0] if stops else "", stops))
        for left in options:
            for right in options:
                assert checker.meets(left, right) == (downstream_similarity(left, right) >= threshold)
