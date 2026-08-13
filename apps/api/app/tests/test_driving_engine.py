"""Tests for the rebuilt Driving engine (Part 7 + Part 11 acceptance criteria)."""

from __future__ import annotations

from datetime import datetime

import pytest

import app.engines.driving.major_road_access as major_road_access_module
from app.adapters.routing.base import (
    RouteMatrixResponse,
    RouteMode,
    RouteResult,
    RouteStep,
    RoutingProvider,
    RoutingProviderError,
)
from app.adapters.transport_data.major_road_network import (
    DriveEdge,
    DriveNode,
    LocalDriveGraph,
    MajorRoadMapping,
    PrecomputedMajorRoad,
    SingaporeDriveGraphStore,
    SlaMajorRoad,
    SlaMajorRoadStore,
    SlaOsmMajorRoadMappingStore,
    find_candidate_sla_major_roads,
    find_major_road_entry_nodes,
    match_sla_road_to_osm_edges,
)
from app.domain.enums import ComponentStatus
from app.domain.transport_models import ComponentResult, build_rollup, not_assessed
from app.engines.driving.engine import compute_driving_model
from app.engines.driving.major_road_access import MajorRoadAccessOutcome, compute_major_road_access
from app.engines.driving.major_road_geometry import validate_sustained_major_road_entry
from app.engines.driving.parking_convenience import compute_parking_convenience
from app.engines.driving.peak_access_penalty import compute_peak_access_penalty
from app.engines.driving.route_connectivity import compute_route_connectivity
from app.engines.transport_config import DrivingConfig
from app.tests.routing_helpers import (
    DHOBY_GHAUT,
    TAMPINES,
    AlwaysFailingRoutingProvider,
    FixedDurationRoutingProvider,
    _driving_polyline,
)

ROAD_A = SlaMajorRoad("sla-a", "Alpha Avenue", (((103.8000, 1.3000), (103.8100, 1.3000)),))
ROAD_B = SlaMajorRoad("sla-b", "Bravo Road", (((103.7990, 1.3020), (103.8100, 1.3020)),))
SYNTHETIC_ROADS = (ROAD_A, ROAD_B)
SYNTHETIC_GRAPH = LocalDriveGraph(
    [
        DriveNode("origin", 1.3000, 103.7990),
        DriveNode("entry-a", 1.3000, 103.8000),
        DriveNode("on-a", 1.3000, 103.8050),
        DriveNode("entry-b", 1.3020, 103.8015),
        DriveNode("on-b", 1.3020, 103.8050),
    ],
    [
        DriveEdge("origin", "entry-a", "local-a", ((103.7990, 1.3000), (103.8000, 1.3000)), "Local Street", 400),
        DriveEdge("entry-a", "on-a", "major-a", ((103.8000, 1.3000), (103.8050, 1.3000)), "Alpha Avenue", 500),
        DriveEdge("origin", "entry-b", "local-b", ((103.7990, 1.3000), (103.8015, 1.3020)), "Local Street", 900),
        DriveEdge("entry-b", "on-b", "major-b", ((103.8015, 1.3021), (103.8050, 1.3021)), "Bravo Road", 500),
    ],
)


def _mapping_for(roads: tuple[SlaMajorRoad, ...], graph: LocalDriveGraph) -> MajorRoadMapping:
    entries_by_road: dict[str, PrecomputedMajorRoad] = {}
    for road in roads:
        matched = match_sla_road_to_osm_edges(road, graph, 35, 12)
        entries = find_major_road_entry_nodes(road, matched, graph, 40, 8)
        entries_by_road[road.identifier] = PrecomputedMajorRoad(
            identifier=road.identifier,
            name=road.name,
            matched_edge_ids=tuple(edge.identifier for edge in matched),
            entry_nodes=tuple(entries),
            name_supported_edge_count=len(matched),
        )
    return MajorRoadMapping("test-graph", "test-sla", "test", "test", 100.0, entries_by_road)


SYNTHETIC_MAPPING = _mapping_for(SYNTHETIC_ROADS, SYNTHETIC_GRAPH)


@pytest.fixture(autouse=True)
def _local_major_road_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Driving tests never depend on a downloaded national OSM/SLA artifact."""
    monkeypatch.setattr(SlaMajorRoadStore, "load", staticmethod(lambda: SYNTHETIC_ROADS))
    monkeypatch.setattr(SingaporeDriveGraphStore, "load", staticmethod(lambda: SYNTHETIC_GRAPH))
    monkeypatch.setattr(SlaOsmMajorRoadMappingStore, "load", staticmethod(lambda: SYNTHETIC_MAPPING))


class _DirectionalDurationProvider(RoutingProvider):
    """Returns a duration keyed by destination name-recognisable coordinate,
    so a geographically closer point can be made deliberately slower than a
    farther one — proving selection uses routed duration, not distance."""

    provider_name = "DIRECTIONAL_STUB"

    def __init__(self, durations: dict[tuple[float, float], float]) -> None:
        self.durations = durations
        self.driving_calls: list[tuple[tuple[float, float], tuple[float, float]]] = []

    def _duration_for(self, destination: tuple[float, float]) -> float:
        for coords, minutes in self.durations.items():
            # Catalogue targets sit shortly downstream of the historical
            # junction fixture, so recognise either coordinate as that entry.
            if abs(coords[0] - destination[0]) < 0.001 and abs(coords[1] - destination[1]) < 0.001:
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
        self.driving_calls.append((origin, destination))
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
            encoded_polyline=_driving_polyline(origin, destination),
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
    def test_google_duration_selects_farther_entry_over_shorter_osm_distance(self) -> None:
        provider = _DirectionalDurationProvider(
            {
                (1.3000, 103.8000): 3.0,
                (1.3020, 103.8015): 2.0,
            }
        )
        outcome = compute_major_road_access(1.3000, 103.7990, provider)
        assert outcome.selected_point is not None
        assert outcome.selected_point.name == "Bravo Road"
        assert outcome.result.value["routed_duration_minutes"] == 2.0

    def test_google_receives_actual_listing_coordinate_and_entry_node_coordinate(self) -> None:
        listing = (1.3004, 103.7994)
        provider = _DirectionalDurationProvider({(1.3000, 103.8000): 3.0, (1.3020, 103.8015): 2.0})
        compute_major_road_access(*listing, provider)
        assert {origin for origin, _destination in provider.driving_calls} == {listing}
        assert {destination for _origin, destination in provider.driving_calls} == {
            point.routing_coordinate
            for road in SYNTHETIC_MAPPING.roads_by_identifier.values()
            for point in road.entry_nodes
        }

    def test_candidate_selection_applies_safety_bound_only_after_distance_sorting(self) -> None:
        candidates = find_candidate_sla_major_roads(SYNTHETIC_ROADS, 1.3000, 103.7990, 150, 5)
        assert candidates == [ROAD_A]

    def test_runtime_uses_only_precomputed_entries_for_five_nearest_distinct_major_roads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        roads = tuple(
            SlaMajorRoad(
                f"road-{index}",
                f"Major Road {index}",
                (((103.8000 + index * 0.001, 1.3000), (103.8005 + index * 0.001, 1.3000)),),
            )
            for index in range(6)
        )
        candidates = find_candidate_sla_major_roads(roads, 1.3000, 103.7990, 15_000, 5)
        assert [road.identifier for road in candidates] == ["road-0", "road-1", "road-2", "road-3", "road-4"]
        mapping = MajorRoadMapping("test-graph", "test-sla", "test", "test", 100.0, {})
        monkeypatch.setattr(
            major_road_access_module,
            "match_sla_road_to_osm_edges",
            lambda *_args, **_kwargs: pytest.fail("runtime must not perform SLA-to-OSM matching"),
            raising=False,
        )
        outcome = compute_major_road_access(
            1.3000, 103.7990, FixedDurationRoutingProvider(), roads=roads, mapping=mapping
        )
        assert outcome.result.status == ComponentStatus.NOT_ASSESSED

    def test_candidate_selection_keeps_a_far_road_inside_safety_bound(self) -> None:
        far_road = SlaMajorRoad("far", "Far Major Road", (((103.7990, 1.3700), (103.8050, 1.3700)),))
        candidates = find_candidate_sla_major_roads((far_road,), 1.3000, 103.7990, 15_000, 5)
        assert candidates == [far_road]

    def test_candidate_selection_uses_all_available_roads_when_fewer_than_limit(self) -> None:
        candidates = find_candidate_sla_major_roads(SYNTHETIC_ROADS, 1.3000, 103.7990, 15_000, 5)
        assert candidates == [ROAD_A, ROAD_B]

    def test_duplicate_sla_segments_do_not_take_multiple_candidate_slots(self) -> None:
        fragments = (
            SlaMajorRoad("fragment-a", "Shared Major Road", (((103.8000, 1.3000), (103.8010, 1.3000)),)),
            SlaMajorRoad("fragment-b", "Shared Major Road", (((103.8010, 1.3000), (103.8020, 1.3000)),)),
            ROAD_B,
        )
        candidates = find_candidate_sla_major_roads(fragments, 1.3000, 103.7990, 15_000, 5)
        assert [road.name for road in candidates] == ["Shared Major Road", "Bravo Road"]

    def test_far_major_road_beyond_old_radius_returns_a_routed_result(self) -> None:
        far_road = SlaMajorRoad("far", "Far Major Road", (((103.7990, 1.3700), (103.8050, 1.3700)),))
        graph = LocalDriveGraph(
            [
                DriveNode("origin", 1.3000, 103.7990),
                DriveNode("entry", 1.3700, 103.7990),
                DriveNode("on-major", 1.3700, 103.8050),
            ],
            [
                DriveEdge("origin", "entry", "local", ((103.7990, 1.3000), (103.7990, 1.3700)), "Local Street", 8100),
                DriveEdge(
                    "entry", "on-major", "major", ((103.7990, 1.3700), (103.8050, 1.3700)), "Far Major Road", 500
                ),
            ],
        )
        outcome = compute_major_road_access(
            1.3000,
            103.7990,
            FixedDurationRoutingProvider(),
            roads=(far_road,),
            mapping=_mapping_for((far_road,), graph),
        )
        assert outcome.result.status == ComponentStatus.CALCULATED
        assert outcome.selected_point is not None
        assert outcome.selected_point.name == "Far Major Road"
        assert outcome.result.value["routed_distance_metres"] == 333

    def test_failed_google_candidate_route_does_not_block_farther_entry(self) -> None:
        class OneFailureProvider(_DirectionalDurationProvider):
            def get_driving_route(self, origin, destination, departure_time, traffic_aware=True):
                if abs(destination[0] - 1.3000) < 0.0001 and abs(destination[1] - 103.8007) < 0.0001:
                    raise RoutingProviderError("no route", provider=self.provider_name)
                return super().get_driving_route(origin, destination, departure_time, traffic_aware)

        provider = OneFailureProvider({(1.3020, 103.8015): 2.5})
        outcome = compute_major_road_access(1.3000, 103.7990, provider)
        assert outcome.result.status == ComponentStatus.CALCULATED
        assert outcome.selected_point is not None
        assert outcome.selected_point.name == "Bravo Road"

    def test_all_google_candidate_routes_unavailable_is_not_assessed(self) -> None:
        outcome = compute_major_road_access(1.3000, 103.7990, AlwaysFailingRoutingProvider())
        assert outcome.result.status == ComponentStatus.NOT_ASSESSED

    def test_equal_google_durations_use_google_distance_tie_breaker(self) -> None:
        class EqualDurationProvider(_DirectionalDurationProvider):
            def get_driving_route(self, origin, destination, departure_time, traffic_aware=True):
                route = super().get_driving_route(origin, destination, departure_time, traffic_aware)
                route.duration_minutes = 2.0
                route.distance_metres = 600 if abs(destination[0] - 1.3000) < 0.0001 else 400
                return route

        provider = EqualDurationProvider({(1.3000, 103.8000): 2.0, (1.3020, 103.8015): 2.0})
        outcome = compute_major_road_access(1.3000, 103.7990, provider)
        assert outcome.selected_point is not None
        assert outcome.selected_point.name == "Bravo Road"

    def test_exact_google_duration_and_distance_tie_uses_stable_road_id(self) -> None:
        class ExactTieProvider(_DirectionalDurationProvider):
            def get_driving_route(self, origin, destination, departure_time, traffic_aware=True):
                route = super().get_driving_route(origin, destination, departure_time, traffic_aware)
                route.duration_minutes = 2.0
                route.distance_metres = 400
                return route

        provider = ExactTieProvider({(1.3000, 103.8000): 2.0, (1.3020, 103.8015): 2.0})
        outcome = compute_major_road_access(1.3000, 103.7990, provider)
        assert outcome.selected_point is not None
        assert outcome.selected_point.name == "Alpha Avenue"

    def test_no_road_inside_safety_bound_is_not_assessed(self) -> None:
        too_far = SlaMajorRoad("too-far", "Too Far Road", (((103.7990, 1.5000), (103.8050, 1.5000)),))
        outcome = compute_major_road_access(
            1.3000, 103.7990, FixedDurationRoutingProvider(), roads=(too_far,), mapping=SYNTHETIC_MAPPING
        )
        assert outcome.result.status == ComponentStatus.NOT_ASSESSED

    def test_google_duration_and_distance_are_reported_without_osm_route_calculation(self) -> None:
        outcome = compute_major_road_access(1.3000, 103.7990, FixedDurationRoutingProvider(minutes=7.3))
        assert outcome.result.status == ComponentStatus.CALCULATED
        assert outcome.result.value["routed_duration_minutes"] == 7.3
        assert outcome.result.value["routed_distance_metres"] == 333
        assert outcome.result.value["peak_duration_minutes"] == 7.3

    def test_missing_or_incompatible_mapping_is_not_assessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(SlaOsmMajorRoadMappingStore, "load", staticmethod(lambda: None))
        outcome = compute_major_road_access(*DHOBY_GHAUT, FixedDurationRoutingProvider())
        assert outcome.result.status == ComponentStatus.NOT_ASSESSED
        assert outcome.result.score is None

    def test_precomputed_road_without_valid_entry_is_not_assessed(self) -> None:
        disconnected_graph = LocalDriveGraph(
            list(SYNTHETIC_GRAPH.nodes.values()),
            [edge for edge in SYNTHETIC_GRAPH.edges if edge.source != "origin"],
        )
        outcome = compute_major_road_access(
            1.3000, 103.7990, FixedDurationRoutingProvider(), mapping=_mapping_for(SYNTHETIC_ROADS, disconnected_graph)
        )
        assert outcome.result.status == ComponentStatus.NOT_ASSESSED

    def test_slight_offset_and_matching_name_match_but_parallel_road_does_not(self) -> None:
        graph = LocalDriveGraph(
            [DriveNode("a", 1.3000, 103.8000), DriveNode("b", 1.3003, 103.8050)],
            [
                DriveEdge("a", "b", "correct", ((103.8000, 1.3003), (103.8050, 1.3003)), "Alpha Avenue", 500),
                DriveEdge("a", "b", "parallel", ((103.8000, 1.30025), (103.8050, 1.30025)), "Other Road", 500),
            ],
        )
        matches = match_sla_road_to_osm_edges(ROAD_A, graph, 35, 12)
        assert [edge.key for edge in matches] == ["correct"]

    def test_crossing_geometry_without_a_graph_junction_is_not_an_entry(self) -> None:
        graph = LocalDriveGraph(
            [
                DriveNode("local-start", 1.2990, 103.8050),
                DriveNode("local-end", 1.3010, 103.8050),
                DriveNode("major-start", 1.3000, 103.8000),
                DriveNode("major-end", 1.3000, 103.8100),
            ],
            [
                DriveEdge(
                    "local-start",
                    "local-end",
                    "local",
                    ((103.8050, 1.2990), (103.8050, 1.3010)),
                    "Local Street",
                    200,
                ),
                DriveEdge(
                    "major-start",
                    "major-end",
                    "major",
                    ((103.8000, 1.3000), (103.8100, 1.3000)),
                    "Alpha Avenue",
                    500,
                ),
            ],
        )
        entries = find_major_road_entry_nodes(ROAD_A, [graph.edges[1]], graph, 40, 8)
        assert entries == []

    def test_directed_entry_topology_still_uses_the_entry_node_u(self) -> None:
        graph = LocalDriveGraph(
            list(SYNTHETIC_GRAPH.nodes.values()),
            [edge for edge in SYNTHETIC_GRAPH.edges if edge.source != "origin"]
            + [DriveEdge("on-a", "entry-b", "approach", ((103.8050, 1.3000), (103.8015, 1.3020)), "Local Street", 400)],
        )
        provider = _DirectionalDurationProvider({(1.3020, 103.8015): 2.0})
        outcome = compute_major_road_access(1.3000, 103.7990, provider, mapping=_mapping_for(SYNTHETIC_ROADS, graph))
        assert outcome.result.status == ComponentStatus.CALCULATED
        assert outcome.selected_point is not None
        assert outcome.selected_point.node_id == "entry-b"
        assert provider.driving_calls[0][1] == outcome.selected_point.routing_coordinate

    def test_fastest_summary_route_without_sustained_entry_is_rejected_for_next_valid_candidate(self) -> None:
        class CrossingFirstProvider(_DirectionalDurationProvider):
            def get_driving_route(self, origin, destination, departure_time, traffic_aware=True):
                route = super().get_driving_route(origin, destination, departure_time, traffic_aware)
                if abs(destination[0] - 1.3) < 0.0001:
                    route.encoded_polyline = _driving_polyline((1.299, 103.805), (1.301, 103.805))
                return route

        provider = CrossingFirstProvider({(1.3000, 103.8000): 1.0, (1.3020, 103.8015): 2.0})
        outcome = compute_major_road_access(1.3000, 103.7990, provider)
        assert outcome.selected_point is not None
        assert outcome.selected_point.name == "Bravo Road"

    def test_all_geometry_invalid_candidates_are_not_assessed(self) -> None:
        class CrossingProvider(_DirectionalDurationProvider):
            def get_driving_route(self, origin, destination, departure_time, traffic_aware=True):
                route = super().get_driving_route(origin, destination, departure_time, traffic_aware)
                route.encoded_polyline = _driving_polyline((1.299, 103.805), (1.301, 103.805))
                return route

        outcome = compute_major_road_access(
            1.3000, 103.7990, CrossingProvider({(1.3000, 103.8000): 1.0, (1.3020, 103.8015): 2.0})
        )
        assert outcome.result.status == ComponentStatus.NOT_ASSESSED


class TestSustainedMajorRoadEntryGeometry:
    def _route(self, points: list[tuple[float, float]]) -> RouteResult:
        return RouteResult(
            2.0, 200, None, None, [], "TEST", None, None, True, encoded_polyline=_driving_polyline(*points)
        )

    def test_crossing_and_parallel_route_are_rejected_but_sustained_aligned_route_is_accepted(self) -> None:
        entry = SYNTHETIC_MAPPING.road_for("sla-a").entry_nodes[0]  # type: ignore[union-attr]
        crossing = self._route([(1.299, 103.805), (1.301, 103.805)])
        parallel = self._route([(1.30025, 103.799), (1.30025, 103.807)])
        sustained = self._route([(1.3000, 103.799), (1.3000, 103.807)])
        assert validate_sustained_major_road_entry(crossing, ROAD_A, entry).valid is False
        assert validate_sustained_major_road_entry(parallel, ROAD_A, entry).valid is False
        validation = validate_sustained_major_road_entry(sustained, ROAD_A, entry)
        assert validation.valid is True
        assert validation.sustained_overlap_metres >= 60


class TestPeakAccessPenalty:
    def test_uses_the_same_access_point_for_both_measurements(self) -> None:
        stub = FixedDurationRoutingProvider(minutes=5.0)
        outcome = compute_major_road_access(1.3000, 103.7990, stub)
        result = compute_peak_access_penalty(1.3000, 103.7990, stub, outcome)
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
        outcome = compute_major_road_access(1.3000, 103.7990, provider)
        result = compute_peak_access_penalty(1.3000, 103.7990, provider, outcome)
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
        outcome = compute_major_road_access(1.3000, 103.7990, provider)
        result = compute_route_connectivity(1.3000, 103.7990, provider, outcome)
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
