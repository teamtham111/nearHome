"""Exhaustively validate the offline SLA Major Road → OSM mapping.

This command is development/data-quality tooling. It never runs in enrichment.
It compares exhaustive matching with STRtree candidate discovery using the same
precise matcher, emits per-road diagnostics, map-debug GeoJSON and evaluates
only human-supplied gold labels (if any).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

DEFAULT_SLA = ROOT / "data_pipeline" / "fixtures" / "sla_major_roads.geojson"
DEFAULT_GRAPH = ROOT / "data_pipeline" / "fixtures" / "singapore-drive.graphml"
DEFAULT_OUTPUT_DIR = ROOT / "data_pipeline" / "validation" / "major_road_mapping"
DEFAULT_GOLD_LABELS = ROOT / "data_pipeline" / "fixtures" / "major_road_mapping_gold_labels.json"


def _edge_id(edge: Any) -> list[str]:
    return list(edge.identifier)


def _edge_id_key(edge_id: list[Any] | tuple[Any, ...]) -> tuple[str, str, str]:
    return tuple(str(part) for part in edge_id)  # type: ignore[return-value]


def _bearing_degrees(line: tuple[tuple[float, float], ...]) -> float | None:
    segments = list(pairwise(line))
    if not segments:
        return None
    start, end = max(segments, key=lambda pair: (pair[1][0] - pair[0][0]) ** 2 + (pair[1][1] - pair[0][1]) ** 2)
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == dy == 0:
        return None
    return math.degrees(math.atan2(dy, dx)) % 180


def _heading_difference(road: Any, edge: Any) -> float | None:
    edge_bearing = _bearing_degrees(edge.coordinates)
    bearings = [_bearing_degrees(line) for line in road.lines]
    valid = [bearing for bearing in bearings if bearing is not None]
    if edge_bearing is None or not valid:
        return None
    return min(abs(edge_bearing - bearing) for bearing in valid for bearing in (bearing, bearing + 180))


def _component_count(edges: list[Any]) -> int:
    if not edges:
        return 0
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        adjacency[edge.source].add(edge.target)
        adjacency[edge.target].add(edge.source)
    seen: set[str] = set()
    components = 0
    for node in adjacency:
        if node in seen:
            continue
        components += 1
        stack = [node]
        seen.add(node)
        while stack:
            current = stack.pop()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
    return components


def _coverage_fraction(
    road: Any, accepted: list[Any], index: Any, tolerance: float
) -> tuple[float | None, float, float]:
    """Estimate SLA line coverage in EPSG:3414; diagnostic only, never a matcher rule."""
    from shapely.geometry import MultiLineString
    from shapely.ops import unary_union

    sla = MultiLineString(
        [[index.transformer.transform(lon, lat) for lon, lat in line] for line in road.lines if len(line) >= 2]
    )
    if sla.is_empty or sla.length == 0:
        return None, 0.0, 0.0
    geometry_by_id = {edge.identifier: geometry for edge, geometry in zip(index.edges, index.geometries, strict=True)}
    accepted_geometries = [geometry_by_id[edge.identifier] for edge in accepted]
    if not accepted_geometries:
        return 0.0, sla.length, 0.0
    matched = unary_union(accepted_geometries)
    coverage = sla.intersection(matched.buffer(tolerance)).length / sla.length
    return min(1.0, coverage), sla.length, matched.length


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _road_debug_features(
    road: Any, candidates: list[Any], evaluations: list[Any], needs_review: bool
) -> list[dict[str, Any]]:
    if not needs_review:
        return []
    features: list[dict[str, Any]] = []
    for line in road.lines:
        features.append(
            _feature(
                {"type": "LineString", "coordinates": line},
                {"kind": "sla_major_road", "sla_road_id": road.identifier, "sla_road_name": road.name},
            )
        )
    evaluation_by_id = {evaluation.edge.identifier: evaluation for evaluation in evaluations}
    for edge in candidates:
        evaluation = evaluation_by_id[edge.identifier]
        features.append(
            _feature(
                {"type": "LineString", "coordinates": edge.coordinates},
                {
                    "kind": "accepted_osm_edge" if evaluation.accepted else "rejected_strtree_candidate",
                    "sla_road_id": road.identifier,
                    "sla_road_name": road.name,
                    "osm_edge_id": _edge_id(edge),
                    "osm_name": edge.name,
                    "distance_metres": round(evaluation.distance_metres, 3),
                    "name_supported": evaluation.name_supported,
                    "required_tolerance_metres": evaluation.required_tolerance_metres,
                    "aligned": evaluation.aligned,
                    "heading_difference_degrees": _heading_difference(road, edge),
                    "rejection_reason": evaluation.rejection_reason,
                },
            )
        )
    return features


def _load_gold_labels(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1 or not isinstance(payload.get("labels"), list):
        raise ValueError("Gold labels must use schema_version=1 and a labels list.")
    return payload["labels"]


def _gold_metrics(
    labels: list[dict[str, Any]], matched_by_road: dict[str, set[tuple[str, str, str]]]
) -> dict[str, Any]:
    true_positive = false_positive = false_negative = 0
    for label in labels:
        identifier = str(label["sla_road_id"])
        expected = {_edge_id_key(edge) for edge in label["expected_osm_edges"]}
        actual = matched_by_road.get(identifier, set())
        true_positive += len(expected & actual)
        false_positive += len(actual - expected)
        false_negative += len(expected - actual)
    if not labels:
        return {
            "labelled_roads": 0,
            "true_positives": None,
            "false_positives": None,
            "false_negatives": None,
            "precision": None,
            "recall": None,
            "f1": None,
        }
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "labelled_roads": len(labels),
        "true_positives": true_positive,
        "false_positives": false_positive,
        "false_negatives": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate STRtree SLA Major Road matching against exhaustive matching")
    parser.add_argument("--sla", type=Path, default=DEFAULT_SLA)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--gold-labels", type=Path, default=DEFAULT_GOLD_LABELS)
    parser.add_argument("--search-radius-metres", type=float, default=100.0)
    parser.add_argument(
        "--road",
        action="append",
        default=[],
        help="Validate one SLA road by identifier or name. Repeat for a focused QA subset.",
    )
    args = parser.parse_args()
    if args.search_radius_metres <= 0:
        raise SystemExit("--search-radius-metres must be positive")

    from app.adapters.transport_data.major_road_network import (
        LocalDriveGraph,
        SlaMajorRoadStore,
        _road_bounds,
        build_osm_edge_strtree,
        evaluate_sla_osm_edge_match,
        strtree_candidate_edges,
    )
    from app.core.config import settings
    from app.engines.transport_config import DRIVING_CONFIG

    settings.sla_major_roads_path = str(args.sla)
    SlaMajorRoadStore.reset_cache()
    roads = SlaMajorRoadStore.load()
    if args.road:
        requested = {value.strip().upper() for value in args.road if value.strip()}
        available = {road.identifier.upper() for road in roads} | {road.name.upper() for road in roads}
        unknown = sorted(requested - available)
        if unknown:
            raise SystemExit(f"Unknown SLA Major Road name/identifier: {', '.join(unknown)}")
        roads = tuple(road for road in roads if road.identifier.upper() in requested or road.name.upper() in requested)
    graph = LocalDriveGraph.from_graphml(args.graph)
    index = build_osm_edge_strtree(graph)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    debug_features: list[dict[str, Any]] = []
    exact_roads = mismatched_roads = total_brute = total_strtree = candidate_total = strict_candidate_total = (
        recall_hits
    ) = strict_recall_hits = 0
    matched_by_road: dict[str, set[tuple[str, str, str]]] = {}

    for number, road in enumerate(roads, start=1):
        road_bounds = _road_bounds(road)
        brute_evaluations = [
            evaluate_sla_osm_edge_match(
                road,
                edge,
                DRIVING_CONFIG.major_road_osm_match_tolerance_m,
                DRIVING_CONFIG.major_road_osm_spatial_only_tolerance_m,
                road_bounds=road_bounds,
            )
            for edge in graph.edges
        ]
        brute = [evaluation.edge for evaluation in brute_evaluations if evaluation.accepted]
        candidates, projected_road = strtree_candidate_edges(road, index, args.search_radius_metres)
        geometry_by_id = {
            edge.identifier: geometry for edge, geometry in zip(index.edges, index.geometries, strict=True)
        }
        strict_candidates = [
            edge
            for edge in candidates
            if geometry_by_id[edge.identifier].distance(projected_road)
            <= DRIVING_CONFIG.major_road_osm_match_tolerance_m
        ]
        candidate_evaluations = [
            evaluate_sla_osm_edge_match(
                road,
                edge,
                DRIVING_CONFIG.major_road_osm_match_tolerance_m,
                DRIVING_CONFIG.major_road_osm_spatial_only_tolerance_m,
                road_bounds=road_bounds,
            )
            for edge in candidates
        ]
        # This is the same final candidate list used by build_major_road_mapping:
        # broad 100 m STRtree retrieval followed by a projected 35 m exclusion,
        # then the canonical precise matcher.
        strict_ids = {edge.identifier for edge in strict_candidates}
        indexed_evaluations = [
            evaluation for evaluation in candidate_evaluations if evaluation.edge.identifier in strict_ids
        ]
        indexed = [evaluation.edge for evaluation in indexed_evaluations if evaluation.accepted]
        brute_ids = {edge.identifier for edge in brute}
        candidate_ids = {edge.identifier for edge in candidates}
        indexed_ids = {edge.identifier for edge in indexed}
        missing, unexpected = sorted(brute_ids - indexed_ids), sorted(indexed_ids - brute_ids)
        exact = not missing and not unexpected
        exact_roads += int(exact)
        mismatched_roads += int(not exact)
        total_brute += len(brute_ids)
        total_strtree += len(indexed_ids)
        candidate_total += len(candidates)
        strict_candidate_total += len(strict_candidates)
        recall_hits += len(brute_ids & candidate_ids)
        strict_recall_hits += len(brute_ids & strict_ids)
        matched_by_road[road.identifier] = brute_ids

        distances = [evaluation.distance_metres for evaluation in brute_evaluations if evaluation.accepted]
        heading_differences = [
            difference for edge in brute if (difference := _heading_difference(road, edge)) is not None
        ]
        geometry_only = [
            evaluation for evaluation in brute_evaluations if evaluation.accepted and not evaluation.name_supported
        ]
        components = _component_count(brute)
        coverage, sla_length, matched_length = _coverage_fraction(
            road, brute, index, DRIVING_CONFIG.major_road_osm_match_tolerance_m
        )
        flags: list[str] = []
        if missing or unexpected:
            flags.append("strtree_mismatch")
        if not brute:
            flags.append("no_accepted_edges")
        if geometry_only:
            flags.append("geometry_only_matches")
        if components > 1:
            flags.append("disconnected_matched_edge_groups")
        if coverage is not None and coverage < 0.8:
            flags.append("low_sla_coverage")
        if matched_length > max(1.0, sla_length) * 2.5:
            flags.append("matched_osm_length_far_exceeds_sla_length")
        if any(distance >= 0.9 * DRIVING_CONFIG.major_road_osm_match_tolerance_m for distance in distances):
            flags.append("near_named_distance_threshold")
        if any(
            evaluation.distance_metres >= 0.9 * DRIVING_CONFIG.major_road_osm_spatial_only_tolerance_m
            for evaluation in geometry_only
        ):
            flags.append("near_geometry_only_distance_threshold")
        if any(difference > 45 for difference in heading_differences):
            flags.append("abrupt_heading_difference")
        if len(brute) > 500:
            flags.append("unusually_many_accepted_edges")

        row = {
            "sla_road_id": road.identifier,
            "sla_road_name": road.name,
            "brute_force_accepted_edge_count": len(brute_ids),
            "strtree_accepted_edge_count": len(indexed_ids),
            "strtree_candidate_count": len(candidates),
            "strtree_strict_geometry_candidate_count": len(strict_candidates),
            "candidate_recall": len(brute_ids & candidate_ids) / len(brute_ids) if brute_ids else 1.0,
            "missing_edge_ids": [list(edge) for edge in missing],
            "unexpected_edge_ids": [list(edge) for edge in unexpected],
            "matched_osm_length_metres": round(matched_length, 2),
            "sla_length_metres": round(sla_length, 2),
            "sla_coverage_fraction": None if coverage is None else round(coverage, 5),
            "median_distance_metres": None if not distances else round(median(distances), 4),
            "maximum_distance_metres": None if not distances else round(max(distances), 4),
            "median_heading_difference_degrees": None
            if not heading_differences
            else round(median(heading_differences), 4),
            "maximum_heading_difference_degrees": None if not heading_differences else round(max(heading_differences), 4),
            "name_supported_edge_count": len(brute) - len(geometry_only),
            "geometry_only_edge_count": len(geometry_only),
            "matched_edge_components": components,
            "needs_review": bool(flags),
            "review_reasons": flags,
        }
        rows.append(row)
        debug_features.extend(_road_debug_features(road, candidates, candidate_evaluations, bool(flags)))
        print(f"Validated {number}/{len(roads)}: {road.name}", flush=True)

    labels = _load_gold_labels(args.gold_labels)
    gold = _gold_metrics(labels, matched_by_road)
    report = {
        "dataset": {
            "sla_roads_tested": len(roads),
            "osm_drivable_edges": len(graph.edges),
            "sla_geometry_crs": "EPSG:4326 GeoJSON coordinates (longitude, latitude)",
            "osm_geometry_crs": "EPSG:4326 OSMnx GraphML x/y (longitude, latitude)",
            "strtree_matching_crs": "EPSG:3414 / SVY21 metres",
            "precise_match_metric": "existing local equirectangular metres-per-degree helper",
        },
        "strtree_correctness": {
            "roads_identical": exact_roads,
            "roads_different": mismatched_roads,
            "total_brute_force_accepted_edges": total_brute,
            "total_strtree_accepted_edges": total_strtree,
            "candidate_recall": recall_hits / total_brute if total_brute else 1.0,
            "strict_candidate_recall": strict_recall_hits / total_brute if total_brute else 1.0,
            "average_candidates_before": len(graph.edges),
            "average_candidates_after_strtree": candidate_total / len(roads) if roads else 0,
            "average_candidates_after_strict_geometry_prefilter": strict_candidate_total / len(roads) if roads else 0,
            "candidate_reduction_fraction": 1 - candidate_total / (len(roads) * len(graph.edges))
            if roads and graph.edges
            else 0,
        },
        "underlying_matcher": {
            "total_accepted_edges": total_brute,
            "roads_automatically_flagged": sum(row["needs_review"] for row in rows),
        },
        "gold_standard_validation": gold,
        "limitations": [
            (
                "Exact STRtree/brute-force equality validates candidate discovery only; "
                "it does not establish real-world map accuracy."
            ),
            "No human-verified gold labels are supplied by this repository unless the labels file is populated.",
            (
                "The final matcher retains its existing local metres-per-degree distance implementation; "
                "EPSG:3414 is used for STRtree buffering/candidates."
            ),
        ],
    }
    (args.output_dir / "validation_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    (args.output_dir / "road_diagnostics.json").write_text(json.dumps(rows, indent=2, sort_keys=True))
    suspicious = [row for row in rows if row["needs_review"]]
    (args.output_dir / "suspicious_roads.json").write_text(json.dumps(suspicious, indent=2, sort_keys=True))
    with (args.output_dir / "road_diagnostics.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["sla_road_id"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {key: json.dumps(value) if isinstance(value, (list, dict)) else value for key, value in row.items()}
            )
    (args.output_dir / "suspicious_roads_debug.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": debug_features})
    )
    print(json.dumps(report["strtree_correctness"], indent=2))
    print(f"Wrote validation artifacts to {args.output_dir}")
    return 1 if mismatched_roads else 0


if __name__ == "__main__":
    raise SystemExit(main())
