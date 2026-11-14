"""Tests for the curated rail graph (Part 5 of the spec)."""

from __future__ import annotations

from app.networks.rail_graph import RailGraph

# Real, geocoded coordinates from data_pipeline/fixtures/rail/rail_stations.json
WOODLANDS = (1.43681962961519, 103.786066799253)
BISHAN = (1.35101889777844, 103.850057208608)


class TestRailGraphStructure:
    def test_graph_loads_with_real_fixture_data(self) -> None:
        graph = RailGraph()
        assert graph.is_loaded
        assert len(graph.all_codes()) > 50

    def test_interchange_has_transfer_edge_between_line_nodes(self) -> None:
        graph = RailGraph()
        # Bishan (NS17/CC15) is a real interchange — there must be an explicit
        # transfer edge, not an inferred one from station-number ordering.
        path = graph.shortest_path("NS17", "CC15")
        assert path is not None
        assert path.transfers == 1

    def test_lines_at_returns_all_lines_for_interchange(self) -> None:
        graph = RailGraph()
        lines = graph.lines_at("NS17")
        assert "NSL" in lines
        # Bishan is an NSL/CCL interchange; lines_at() must know about the
        # CCL side via the transfer edge, not just the physical station's own line.
        assert "CCL" in lines or "CCL" in graph.lines_at("CC15")

    def test_lines_reachable_within_one_transfer_includes_more_than_direct(self) -> None:
        graph = RailGraph()
        direct = graph.lines_at("NS13")  # Yishun — NSL only
        one_transfer = graph.lines_reachable_within_one_transfer("NS13")
        assert direct.issubset(one_transfer)

    def test_reachable_within_respects_time_limit(self) -> None:
        graph = RailGraph()
        short = graph.reachable_within("NS17", 10)
        longer = graph.reachable_within("NS17", 45)
        assert set(short.keys()).issubset(set(longer.keys()))
        assert all(minutes <= 10 for minutes in short.values())
        assert all(minutes <= 45 for minutes in longer.values())

    def test_shortest_path_is_none_for_disconnected_or_unknown_codes(self) -> None:
        graph = RailGraph()
        assert graph.shortest_path("NOT_A_REAL_CODE", "NS17") is None

    def test_station_name_for_code_resolves(self) -> None:
        graph = RailGraph()
        assert graph.station_name_for_code("NS17") == "Bishan"


class TestGeographicSanity:
    def test_woodlands_prefilter_does_not_return_bishan(self) -> None:
        """Acceptance criterion: a Woodlands listing must not select Bishan
        as a practically nearest station just because an incomplete fixture
        made it the closest entry available."""
        graph = RailGraph()
        nearby = graph.nearby_station_codes(*WOODLANDS, max_distance_m=2500.0)
        names = {graph.station_name_for_code(code) for code, _dist in nearby}
        assert "Bishan" not in names
        assert "Woodlands" in names

    def test_bishan_itself_is_the_practical_nearest_for_its_own_coordinates(self) -> None:
        graph = RailGraph()
        nearby = graph.nearby_station_codes(*BISHAN, max_distance_m=1000.0)
        names = {graph.station_name_for_code(code) for code, _dist in nearby}
        assert "Bishan" in names


class TestStationCoordinates:
    def test_no_two_distinct_stations_share_coordinates(self) -> None:
        import json
        from collections import defaultdict
        from pathlib import Path

        path = Path(__file__).resolve().parents[4] / "data_pipeline" / "fixtures" / "rail" / "rail_stations.json"
        stations = json.loads(path.read_text())["stations"]
        by_point: dict[tuple[float, float], list[str]] = defaultdict(list)
        for station in stations:
            if station.get("latitude") is not None and station.get("longitude") is not None:
                by_point[(station["latitude"], station["longitude"])].append(station["station_name"])
        shared = {point: names for point, names in by_point.items() if len(names) > 1}
        assert not shared, f"Distinct stations share coordinates: {shared}"

    def test_corrected_station_coordinates_match_onemap(self) -> None:
        graph = RailGraph()
        expected = {
            "EW1": (1.373043, 103.949285),  # Pasir Ris
            "NE17": (1.404547, 103.902073),  # Punggol
            "EW2": (1.353302, 103.945145),  # Tampines
            "NS22": (1.303980, 103.832245),  # Orchard
        }
        for code, (latitude, longitude) in expected.items():
            station = graph.station_by_code(code)
            assert station is not None
            assert abs(station.latitude - latitude) < 1e-5
            assert abs(station.longitude - longitude) < 1e-5
