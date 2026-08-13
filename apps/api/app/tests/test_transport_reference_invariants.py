"""Fixture-level invariants for the curated rail and LTA bus source data.

These checks validate relationships the runtime graph/index builders rely on;
they do not claim the external source datasets are independently complete.
"""

from __future__ import annotations

from collections import defaultdict

from app.adapters.transport_data.lta_bus import LtaBusDataStore
from app.adapters.transport_data.rail_data import RailDataStore
from app.networks.rail_graph import RailGraph


def test_rail_fixture_has_unique_codes_and_edges_reference_existing_nodes() -> None:
    data = RailDataStore.load()
    codes = [code for station in data.stations for code in station.codes]
    assert len(codes) == len(set(codes))
    known_codes = set(codes)
    assert all(edge.from_node in known_codes and edge.to_node in known_codes for edge in data.edges)


def test_rail_edges_respect_physical_station_and_line_invariants() -> None:
    data = RailDataStore.load()
    station_by_code = {code: station for station in data.stations for code in station.codes}
    connected = {code: 0 for code in station_by_code}
    for edge in data.edges:
        connected[edge.from_node] += 1
        connected[edge.to_node] += 1
        assert edge.edge_type in {"ride", "transfer"}
        assert edge.estimated_minutes > 0
        if edge.edge_type == "ride":
            assert edge.line in station_by_code[edge.from_node].lines
            assert edge.line in station_by_code[edge.to_node].lines
        else:
            assert station_by_code[edge.from_node].station_name == station_by_code[edge.to_node].station_name
            assert station_by_code[edge.from_node].is_interchange
            assert station_by_code[edge.to_node].is_interchange
    assert all(connected[code] > 0 for code, station in station_by_code.items() if station.active)


def test_rail_golden_paths_use_required_intermediate_nodes_and_no_shortcut() -> None:
    graph = RailGraph()
    same_line = graph.shortest_path("NS13", "NS17")
    assert same_line is not None
    assert same_line.node_path == ["NS13", "NS14", "NS15", "NS16", "NS17"]
    assert same_line.transfers == 0

    interchange = graph.shortest_path("CC15", "NE6")
    assert interchange is not None
    assert interchange.transfers == 1
    assert "CC13" in interchange.node_path and "NE12" in interchange.node_path

    mrt_to_lrt = graph.shortest_path("NS13", "BP6")
    assert mrt_to_lrt is not None
    assert mrt_to_lrt.transfers == 1
    assert mrt_to_lrt.node_path[-1] == "BP6"
    assert {"BP1", "BP2", "BP3", "BP4", "BP5"}.issubset(mrt_to_lrt.node_path)

    sengkang_lrt = graph.shortest_path("NE16", "SE3")
    assert sengkang_lrt is not None
    assert sengkang_lrt.transfers == 1
    assert sengkang_lrt.line_path[-2:] == ["SKLRT", "SKLRT"]


def test_lta_bus_fixture_routes_reference_stops_and_keep_service_directions_ordered() -> None:
    LtaBusDataStore.reset_cache()
    report = LtaBusDataStore.quality_report()
    assert report.route_stops_referencing_unknown_stop_codes == 0
    assert report.duplicate_stop_sequences == 0

    routes_by_key: dict[tuple[str, int], list] = defaultdict(list)
    for key in LtaBusDataStore.all_service_directions():
        routes_by_key[key].extend(LtaBusDataStore.route_stops(key))
    assert all(
        [row.stop_sequence for row in rows] == sorted(row.stop_sequence for row in rows)
        and len({row.stop_sequence for row in rows}) == len(rows)
        for rows in routes_by_key.values()
    )


def test_real_service_117_directions_have_distinct_stop_orderings() -> None:
    direction_one = LtaBusDataStore.route_stops(("117", 1))
    direction_two = LtaBusDataStore.route_stops(("117", 2))
    assert direction_one and direction_two
    assert [row.bus_stop_code for row in direction_one] != [row.bus_stop_code for row in direction_two]
    assert direction_one[0].bus_stop_code == "69009"
    assert direction_two[0].bus_stop_code == "58009"
