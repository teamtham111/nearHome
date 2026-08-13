#!/usr/bin/env python3
"""Export a small, evidence-faithful Major Road QA sample for QGIS.

This is an offline debugging command. It reads the same immutable SLA/OSM
artifacts that runtime enrichment uses, verifies the persisted mapping against
the current shared candidate routine, and writes only geometries/attributes
that the Major Road pipeline has actually produced.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

DEFAULT_SLA = ROOT / "data_pipeline" / "fixtures" / "sla_major_roads.geojson"
DEFAULT_GRAPH = ROOT / "data_pipeline" / "fixtures" / "singapore-drive.graphml"
DEFAULT_MAPPING = ROOT / "data_pipeline" / "fixtures" / "sla_osm_major_road_mapping.json"
DEFAULT_DIAGNOSTICS = ROOT / "data_pipeline" / "validation" / "major_road_mapping" / "road_diagnostics.json"
DEFAULT_OUTPUT_DIR = ROOT / "nearhome_qgis" / "data"
DEFAULT_ROADS = ("Jalan Bahar", "Woodlands Avenue 8", "Toh Tuck Avenue")


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _write_feature_collection(path: Path, features: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=2) + "\n")


def _edge_properties(road: Any, evaluation: Any) -> dict[str, Any]:
    edge = evaluation.edge
    return {
        "road_id": road.identifier,
        "road_name": road.name,
        "osm_id": ":".join(edge.identifier),
        "osm_source_node": edge.source,
        "osm_target_node": edge.target,
        "osm_edge_key": edge.key,
        "osm_name": edge.name,
        "osm_length_metres": edge.length_metres,
        "distance_metres": round(evaluation.distance_metres, 3),
        "name_supported": evaluation.name_supported,
        "required_tolerance_metres": evaluation.required_tolerance_metres,
        "aligned": evaluation.aligned,
    }


def _candidate_properties(decision: Any, road_review: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": decision.candidate_id,
        "road_id": decision.major_road_id,
        "road_name": decision.name,
        "node_id": decision.node_id,
        "topology_status": decision.status,
        "reason": decision.reason,
        "matched_osm_edge_ids": [list(edge) for edge in decision.matched_edge_ids],
        "approach_osm_edge_ids": [list(edge) for edge in decision.approach_edge_ids],
        "incoming_edge_count": decision.incoming_edge_count,
        "outgoing_edge_count": decision.outgoing_edge_count,
        "has_non_major_approach": bool(decision.approach_edge_ids),
        "road_needs_review": bool(road_review["needs_review"]),
        "road_review_reasons": road_review["review_reasons"],
    }


def _point_feature(decision: Any, properties: dict[str, Any]) -> dict[str, Any]:
    return _feature(
        {"type": "Point", "coordinates": [decision.longitude, decision.latitude]},
        properties,
    )


def _load_road_diagnostics(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"Road diagnostics not found: {path}. Run validate_major_road_mapping.py first.")
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise SystemExit(f"Road diagnostics must be a JSON list: {path}")
    return {str(row["sla_road_id"]): row for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export current NearHome Major Road QA evidence as EPSG:4326 GeoJSON")
    parser.add_argument("--sla", type=Path, default=DEFAULT_SLA)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--diagnostics", type=Path, default=DEFAULT_DIAGNOSTICS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--road", action="append", default=[], help="Road name or ID; repeat up to three times.")
    args = parser.parse_args()
    requested = tuple(args.road) if args.road else DEFAULT_ROADS
    if not 1 <= len(requested) <= 3:
        raise SystemExit("Export one to three roads only.")
    for path in (args.sla, args.graph, args.mapping):
        if not path.is_file():
            raise SystemExit(f"Required pipeline artifact does not exist: {path}")

    from app.adapters.transport_data.major_road_network import (
        LocalDriveGraph,
        SlaMajorRoadStore,
        SlaOsmMajorRoadMappingStore,
        evaluate_sla_osm_edge_match,
        find_major_road_entry_nodes,
    )
    from app.core.config import settings
    from app.engines.transport_config import DRIVING_CONFIG

    settings.sla_major_roads_path = str(args.sla)
    settings.singapore_drive_graph_path = str(args.graph)
    settings.sla_osm_major_road_mapping_path = str(args.mapping)
    SlaMajorRoadStore.reset_cache()
    SlaOsmMajorRoadMappingStore.reset_cache()
    roads_by_name = {road.name.upper(): road for road in SlaMajorRoadStore.load()}
    roads_by_id = {road.identifier.upper(): road for road in roads_by_name.values()}
    selected_roads = []
    for value in requested:
        road = roads_by_name.get(value.upper()) or roads_by_id.get(value.upper())
        if road is None:
            raise SystemExit(f"Unknown SLA Major Road name/identifier: {value}")
        if road not in selected_roads:
            selected_roads.append(road)

    mapping = SlaOsmMajorRoadMappingStore.load()
    if mapping is None:
        raise SystemExit("The persisted SLA/OSM mapping is unavailable or does not match the supplied artifacts.")
    diagnostics_by_road = _load_road_diagnostics(args.diagnostics)
    graph = LocalDriveGraph.from_graphml(args.graph)
    edge_by_id = {edge.identifier: edge for edge in graph.edges}

    sla_features: list[dict[str, Any]] = []
    matched_edge_features: list[dict[str, Any]] = []
    candidate_features: list[dict[str, Any]] = []
    accepted_features: list[dict[str, Any]] = []
    rejected_uncertain_features: list[dict[str, Any]] = []
    summary: dict[str, dict[str, int]] = {}

    for road in selected_roads:
        precomputed = mapping.road_for(road.identifier)
        if precomputed is None:
            raise SystemExit(f"No persisted mapping exists for {road.name}.")
        road_review = diagnostics_by_road.get(road.identifier)
        if road_review is None:
            raise SystemExit(f"No matching diagnostic row exists for {road.name}.")
        matched_edges = [edge_by_id[edge_id] for edge_id in precomputed.matched_edge_ids if edge_id in edge_by_id]
        if len(matched_edges) != len(precomputed.matched_edge_ids):
            raise SystemExit(f"Persisted mapping for {road.name} references OSM edges missing from the supplied graph.")

        for line in road.lines:
            sla_features.append(
                _feature(
                    {"type": "LineString", "coordinates": line},
                    {"road_id": road.identifier, "road_name": road.name},
                )
            )
        for edge in matched_edges:
            evaluation = evaluate_sla_osm_edge_match(
                road,
                edge,
                DRIVING_CONFIG.major_road_osm_match_tolerance_m,
                DRIVING_CONFIG.major_road_osm_spatial_only_tolerance_m,
            )
            if not evaluation.accepted:
                raise SystemExit(f"Persisted matched edge no longer passes the shared matcher: {road.name} {edge.identifier}")
            matched_edge_features.append(
                _feature({"type": "LineString", "coordinates": edge.coordinates}, _edge_properties(road, evaluation))
            )

        topology_decisions: list[Any] = []
        generated_entries = find_major_road_entry_nodes(
            road,
            matched_edges,
            graph,
            DRIVING_CONFIG.major_road_entry_node_dedup_m,
            DRIVING_CONFIG.max_major_road_entry_nodes_per_road,
            DRIVING_CONFIG.major_road_entry_target_distance_m,
            diagnostics=topology_decisions,
        )
        expected_ids = {entry.candidate_id for entry in precomputed.entry_nodes}
        generated_ids = {entry.candidate_id for entry in generated_entries}
        if generated_ids != expected_ids:
            raise SystemExit(f"Shared entry-node output disagrees with the persisted mapping for {road.name}.")

        accepted = rejected = review = 0
        for decision in topology_decisions:
            properties = _candidate_properties(decision, road_review)
            candidate_features.append(_point_feature(decision, properties))
            if decision.status == "ACCEPTED":
                accepted += 1
                accepted_features.append(_point_feature(decision, {**properties, "status": "ACCEPTED"}))
                if road_review["needs_review"]:
                    review += 1
                    rejected_uncertain_features.append(
                        _point_feature(
                            decision,
                            {
                                **properties,
                                "status": "REVIEW",
                                "reason": "road_mapping_review: " + ", ".join(road_review["review_reasons"]),
                            },
                        )
                    )
            else:
                rejected += 1
                rejected_uncertain_features.append(
                    _point_feature(decision, {**properties, "status": "REJECTED"})
                )
        summary[road.name] = {"accepted": accepted, "rejected": rejected, "review": review}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_feature_collection(args.output_dir / "sla_major_roads.geojson", sla_features)
    _write_feature_collection(args.output_dir / "osm_matched_edges.geojson", matched_edge_features)
    _write_feature_collection(args.output_dir / "access_candidates.geojson", candidate_features)
    _write_feature_collection(args.output_dir / "accepted_access.geojson", accepted_features)
    _write_feature_collection(args.output_dir / "rejected_uncertain.geojson", rejected_uncertain_features)
    print(
        json.dumps(
            {
                "roads": summary,
                "feature_counts": {
                    "sla_major_roads": len(sla_features),
                    "osm_matched_edges": len(matched_edge_features),
                    "access_candidates": len(candidate_features),
                    "accepted_access": len(accepted_features),
                    "rejected_uncertain": len(rejected_uncertain_features),
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
