"""Offline SLA→OSM Major Road mapping regression tests."""

from __future__ import annotations

import json

import pytest

import app.adapters.transport_data.major_road_network as network
from app.adapters.transport_data.major_road_network import (
    DriveEdge,
    DriveNode,
    LocalDriveGraph,
    MajorRoadEntryPoint,
    MajorRoadMapping,
    PrecomputedMajorRoad,
    SlaMajorRoad,
    SlaOsmMajorRoadMappingStore,
    _sha256_file,
    build_major_road_mapping,
    build_osm_edge_strtree,
    evaluate_sla_osm_edge_match,
    find_major_road_entry_nodes,
    strtree_candidate_edges,
    write_major_road_mapping,
)
from app.core.config import settings

ROAD = SlaMajorRoad("alpha", "Alpha Avenue", (((103.8000, 1.3000), (103.8100, 1.3000)),))
GRAPH = LocalDriveGraph(
    [
        DriveNode("local", 1.3000, 103.7990),
        DriveNode("entry", 1.3000, 103.8000),
        DriveNode("on-major", 1.3000, 103.8050),
        DriveNode("far-a", 1.3200, 103.8000),
        DriveNode("far-b", 1.3200, 103.8100),
        DriveNode("parallel-a", 1.30025, 103.8000),
        DriveNode("parallel-b", 1.30025, 103.8100),
    ],
    [
        DriveEdge("local", "entry", "local", ((103.7990, 1.3000), (103.8000, 1.3000)), "Local Street", 100),
        DriveEdge("entry", "on-major", "major", ((103.8000, 1.3000), (103.8050, 1.3000)), "Alpha Avenue", 500),
        DriveEdge("far-a", "far-b", "far", ((103.8000, 1.3200), (103.8100, 1.3200)), "Alpha Avenue", 500),
        DriveEdge(
            "parallel-a", "parallel-b", "parallel", ((103.8000, 1.30025), (103.8100, 1.30025)), "Other Road", 500
        ),
    ],
)


@pytest.fixture(autouse=True)
def _reset_mapping_store() -> None:
    SlaOsmMajorRoadMappingStore.reset_cache()
    yield
    SlaOsmMajorRoadMappingStore.reset_cache()


def test_offline_builder_uses_strtree_local_candidates_and_keeps_strict_parallel_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = network.match_sla_road_to_osm_edges
    candidate_sizes: list[int] = []

    def record_candidates(*args, **kwargs):
        candidate_sizes.append(len(list(kwargs["candidate_edges"])))
        return original(*args, **kwargs)

    monkeypatch.setattr(network, "match_sla_road_to_osm_edges", record_candidates)
    mapping, report = build_major_road_mapping(
        (ROAD,),
        GRAPH,
        match_tolerance_metres=35,
        spatial_only_tolerance_metres=12,
        entry_node_dedup_metres=40,
        max_entry_nodes_per_road=8,
        search_radius_metres=100,
    )

    assert candidate_sizes and candidate_sizes[0] < len(GRAPH.edges)
    road = mapping.road_for("alpha")
    assert road is not None
    assert [edge_id[2] for edge_id in road.matched_edge_ids] == ["major"]
    assert [entry.node_id for entry in road.entry_nodes] == ["entry"]
    assert report.unmapped_roads == 0


def _single_road_mapping() -> MajorRoadMapping:
    entry = MajorRoadEntryPoint(
        "Alpha Avenue",
        "alpha",
        "entry",
        1.3,
        103.8,
        (("entry", "on-major", "0"),),
        candidate_id="alpha:entry:on-major:0",
        target_latitude=1.3,
        target_longitude=103.8007,
        approach_edge_ids=(("local", "entry", "0"),),
    )
    road = PrecomputedMajorRoad("alpha", "Alpha Avenue", (("entry", "on-major", "0"),), (entry,), 1)
    return MajorRoadMapping("", "", "2026-08-13T00:00:00Z", network.MAPPING_ALGORITHM_VERSION, 100.0, {"alpha": road})


def test_mapping_store_rejects_graph_hash_mismatch(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph_path = tmp_path / "singapore-drive.graphml"
    sla_path = tmp_path / "sla_major_roads.geojson"
    mapping_path = tmp_path / "mapping.json"
    graph_path.write_bytes(b"graph-v1")
    sla_path.write_bytes(b"sla-v1")
    write_major_road_mapping(
        mapping_path, _single_road_mapping(), graph_sha256=_sha256_file(graph_path), sla_sha256=_sha256_file(sla_path)
    )
    monkeypatch.setattr(settings, "singapore_drive_graph_path", str(graph_path))
    monkeypatch.setattr(settings, "sla_major_roads_path", str(sla_path))
    monkeypatch.setattr(settings, "sla_osm_major_road_mapping_path", str(mapping_path))
    SlaOsmMajorRoadMappingStore.reset_cache()
    assert SlaOsmMajorRoadMappingStore.load() is not None

    graph_path.write_bytes(b"graph-v2")
    SlaOsmMajorRoadMappingStore.reset_cache()
    assert SlaOsmMajorRoadMappingStore.load() is None
    assert "graph hash" in (SlaOsmMajorRoadMappingStore.validation_error() or "")


def test_mapping_store_rejects_malformed_artifact(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph_path = tmp_path / "singapore-drive.graphml"
    sla_path = tmp_path / "sla_major_roads.geojson"
    mapping_path = tmp_path / "mapping.json"
    graph_path.write_bytes(b"graph")
    sla_path.write_bytes(b"sla")
    mapping_path.write_text(json.dumps({"schema_version": 1, "metadata": {}, "major_roads": {}}))
    monkeypatch.setattr(settings, "singapore_drive_graph_path", str(graph_path))
    monkeypatch.setattr(settings, "sla_major_roads_path", str(sla_path))
    monkeypatch.setattr(settings, "sla_osm_major_road_mapping_path", str(mapping_path))
    SlaOsmMajorRoadMappingStore.reset_cache()
    assert SlaOsmMajorRoadMappingStore.load() is None
    assert "Invalid" in (SlaOsmMajorRoadMappingStore.validation_error() or "")


def _parallel_edge(offset_metres: float, *, name: str | None, key: str) -> DriveEdge:
    offset_latitude = offset_metres / 111_320
    return DriveEdge(
        "a",
        "b",
        key,
        ((103.8000, 1.3000 + offset_latitude), (103.8100, 1.3000 + offset_latitude)),
        name,
        500,
    )


@pytest.mark.parametrize(
    ("offset_metres", "expected"),
    [(11.9, True), (12.0, True), (12.1, False)],
)
def test_unnamed_or_name_disagreeing_fallback_has_an_inclusive_12m_boundary(
    offset_metres: float, expected: bool
) -> None:
    evaluation = evaluate_sla_osm_edge_match(
        ROAD, _parallel_edge(offset_metres, name="Other Road", key="fallback"), 35, 12
    )
    assert evaluation.accepted is expected
    assert evaluation.name_supported is False


@pytest.mark.parametrize(
    ("offset_metres", "expected"),
    [(34.9, True), (35.0, True), (35.1, False)],
)
def test_name_supported_matching_has_an_inclusive_35m_boundary(offset_metres: float, expected: bool) -> None:
    evaluation = evaluate_sla_osm_edge_match(
        ROAD, _parallel_edge(offset_metres, name="Alpha Avenue", key="named"), 35, 12
    )
    assert evaluation.accepted is expected
    assert evaluation.name_supported is True


def test_crossing_edge_is_rejected_even_at_zero_intersection_distance() -> None:
    crossing = DriveEdge("a", "b", "crossing", ((103.8050, 1.2990), (103.8050, 1.3010)), "Other Road", 200)
    evaluation = evaluate_sla_osm_edge_match(ROAD, crossing, 35, 12)
    # The existing vertex-to-segment matcher does not treat a segment
    # intersection as line alignment, and rejects the crossing safely.
    assert evaluation.distance_metres > 35
    assert evaluation.accepted is False
    assert evaluation.rejection_reason == "outside_broad_tolerance"


def test_service_road_outside_12m_fallback_is_rejected() -> None:
    evaluation = evaluate_sla_osm_edge_match(ROAD, _parallel_edge(15, name="Service Road", key="service"), 35, 12)
    assert evaluation.accepted is False
    assert evaluation.rejection_reason == "outside_required_tolerance"


def test_strtree_retains_duplicate_geometry_and_directed_edge_identity() -> None:
    duplicate_graph = LocalDriveGraph(
        [DriveNode("a", 1.3, 103.8), DriveNode("b", 1.3, 103.81)],
        [
            DriveEdge("a", "b", "0", ROAD.lines[0], "Alpha Avenue", 500),
            DriveEdge("b", "a", "0", ROAD.lines[0], "Alpha Avenue", 500),
        ],
    )
    candidates, _projected = strtree_candidate_edges(ROAD, build_osm_edge_strtree(duplicate_graph), 100)
    assert [edge.identifier for edge in candidates] == [("a", "b", "0"), ("b", "a", "0")]


def test_strtree_candidate_order_is_stable_across_fresh_indexes() -> None:
    first, _ = strtree_candidate_edges(ROAD, build_osm_edge_strtree(GRAPH), 100)
    second, _ = strtree_candidate_edges(ROAD, build_osm_edge_strtree(GRAPH), 100)
    assert [edge.identifier for edge in first] == [edge.identifier for edge in second]


def test_strtree_handles_multiline_sla_geometry() -> None:
    multiline = SlaMajorRoad(
        "multi",
        "Alpha Avenue",
        (((103.8000, 1.3000), (103.8050, 1.3000)), ((103.8050, 1.3000), (103.8100, 1.3000))),
    )
    candidates, _projected = strtree_candidate_edges(multiline, build_osm_edge_strtree(GRAPH), 100)
    assert {edge.key for edge in candidates} >= {"major", "parallel"}


def test_catalogue_entry_retains_directed_approach_and_downstream_target() -> None:
    mapping, _report = build_major_road_mapping(
        (ROAD,),
        GRAPH,
        match_tolerance_metres=35,
        spatial_only_tolerance_metres=12,
        entry_node_dedup_metres=40,
        max_entry_nodes_per_road=8,
        entry_target_distance_metres=80,
    )
    entry = mapping.road_for("alpha").entry_nodes[0]  # type: ignore[union-attr]
    assert entry.candidate_id == "alpha:entry:on-major:major"
    assert entry.approach_edge_ids == (("local", "entry", "local"),)
    assert entry.routing_coordinate[1] > entry.longitude
    assert (
        70
        < network._point_segment_distance_metres(
            (entry.routing_coordinate[1], entry.routing_coordinate[0]),
            (entry.longitude, entry.latitude),
            (entry.longitude, entry.latitude),
        )
        < 90
    )


def test_entry_node_diagnostics_reuse_the_catalogue_topology_decisions() -> None:
    diagnostics = []
    entries = find_major_road_entry_nodes(
        ROAD,
        [next(edge for edge in GRAPH.edges if edge.key == "major")],
        GRAPH,
        dedup_metres=40,
        limit=8,
        diagnostics=diagnostics,
    )

    assert [entry.node_id for entry in entries] == ["entry"]
    assert len(diagnostics) == 1
    decision = diagnostics[0]
    assert decision.status == "ACCEPTED"
    assert decision.reason is None
    assert decision.approach_edge_ids == (("local", "entry", "local"),)
    assert decision.incoming_edge_count == 1
    assert decision.outgoing_edge_count == 1
