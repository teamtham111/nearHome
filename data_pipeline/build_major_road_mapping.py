"""Precompute SLA Major Road → OSM edges and legal entry nodes.

This is deliberately an offline data-build command. Runtime enrichment only
looks up the resulting versioned JSON and routes a listing to its entry nodes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

DEFAULT_SLA = ROOT / "data_pipeline" / "fixtures" / "sla_major_roads.geojson"
DEFAULT_GRAPH = ROOT / "data_pipeline" / "fixtures" / "singapore-drive.graphml"
DEFAULT_OUTPUT = ROOT / "data_pipeline" / "fixtures" / "sla_osm_major_road_mapping.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the versioned offline SLA-to-OSM Major Road mapping")
    parser.add_argument("--sla", type=Path, default=DEFAULT_SLA)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--search-radius-metres", type=float, default=100.0)
    args = parser.parse_args()
    if args.search_radius_metres <= 0:
        raise SystemExit("--search-radius-metres must be positive")
    if not args.sla.is_file() or not args.graph.is_file():
        raise SystemExit("Both --sla and --graph must exist before building the Major Road mapping.")

    # Imports happen after API_ROOT is added above, allowing this repository
    # script to run without being installed as an application package.
    from app.adapters.transport_data.major_road_network import (
        LocalDriveGraph,
        SlaMajorRoadStore,
        _sha256_file,
        build_major_road_mapping,
        write_major_road_mapping,
    )
    from app.core.config import settings
    from app.engines.transport_config import DRIVING_CONFIG

    # The store is intentionally configured only for this offline command.
    settings.sla_major_roads_path = str(args.sla)
    roads = SlaMajorRoadStore.load()
    graph = LocalDriveGraph.from_graphml(args.graph)
    mapping, report = build_major_road_mapping(
        roads,
        graph,
        match_tolerance_metres=DRIVING_CONFIG.major_road_osm_match_tolerance_m,
        spatial_only_tolerance_metres=DRIVING_CONFIG.major_road_osm_spatial_only_tolerance_m,
        entry_node_dedup_metres=DRIVING_CONFIG.major_road_entry_node_dedup_m,
        max_entry_nodes_per_road=DRIVING_CONFIG.max_major_road_entry_nodes_per_road,
        entry_target_distance_metres=DRIVING_CONFIG.major_road_entry_target_distance_m,
        search_radius_metres=args.search_radius_metres,
        progress_callback=lambda index, total, name: print(
            f"Processed {index}/{total}: {name}", flush=True
        )
        if index == total or index % 25 == 0
        else None,
    )
    write_major_road_mapping(
        args.output,
        mapping,
        graph_sha256=_sha256_file(args.graph),
        sla_sha256=_sha256_file(args.sla),
    )
    print(f"Wrote {args.output}")
    print(
        "Major Road mapping report: "
        f"total={report.total_roads}, mapped={report.mapped_roads}, unmapped={report.unmapped_roads}, "
        f"without_entries={report.roads_without_entries}, matched_edges={report.matched_edges}, "
        f"entry_nodes={report.entry_nodes}, geometry_only_edges={report.geometry_only_edges}"
    )
    if report.suspicious_roads:
        print("Suspicious mappings:")
        for item in report.suspicious_roads:
            print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
