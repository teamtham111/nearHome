"""Build the cached Singapore OSMnx drive graph used by Major-road access.

This is a periodic/offline operation. Application requests load the GraphML
artifact locally and must never issue an Overpass request for a listing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data_pipeline" / "fixtures" / "singapore-drive.graphml"


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and persist Singapore's OSM drivable graph")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        import osmnx as ox
    except ImportError as exc:
        raise SystemExit("Install the API dependencies (including osmnx) before building the drive graph.") from exc
    graph = ox.graph_from_place("Singapore", network_type="drive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(graph, args.output)
    print(f"Wrote {graph.number_of_nodes()} nodes / {graph.number_of_edges()} edges to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
