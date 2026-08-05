#!/usr/bin/env python3
"""Data-quality gate for both the bus-data join and the curated rail graph.

Run after any re-ingestion (`ingest_bus_routes.py`, `ingest_bus_services.py`,
`build_rail_graph.py`) to fail loudly rather than let the Public Transport /
Driving engines silently run on broken reference data.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.adapters.transport_data.lta_bus import LtaBusDataStore  # noqa: E402
from app.adapters.transport_data.rail_data import RailDataStore  # noqa: E402


def validate_bus() -> bool:
    LtaBusDataStore.reset_cache()
    report = LtaBusDataStore.quality_report()
    print("=== Bus data quality ===")
    print(
        f"stops={report.bus_stops_count} routes_rows={report.bus_routes_rows} "
        f"services_rows={report.bus_services_rows} usable={report.is_usable}"
    )
    for p in report.problems:
        print(f"  PROBLEM: {p}")
    return report.is_usable


def validate_rail() -> bool:
    RailDataStore.reset_cache()
    data = RailDataStore.load()
    print("\n=== Rail graph quality ===")
    problems: list[str] = []

    if not data.stations:
        problems.append("No rail stations loaded — run build_rail_graph.py first.")
        print("  PROBLEM: no stations loaded")
        return False

    node_codes = {code for s in data.stations for code in s.codes}
    edge_nodes = {e.from_node for e in data.edges} | {e.to_node for e in data.edges}

    orphans = node_codes - edge_nodes
    if orphans:
        problems.append(f"{len(orphans)} station codes have no edges at all: {sorted(orphans)}")

    unknown_edge_refs = edge_nodes - node_codes
    if unknown_edge_refs:
        problems.append(f"{len(unknown_edge_refs)} edge endpoints reference unknown station codes: {sorted(unknown_edge_refs)}")

    missing_coords = [s.station_name for s in data.stations if s.latitude is None or s.longitude is None]
    if missing_coords:
        problems.append(f"{len(missing_coords)} stations missing geocoded coordinates: {missing_coords}")

    interchange_stations = [s for s in data.stations if len(s.codes) > 1]
    transfer_edge_pairs = {frozenset({e.from_node, e.to_node}) for e in data.edges if e.edge_type == "transfer"}
    missing_interchange_edges = []
    for station in interchange_stations:
        codes = station.codes
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                if frozenset({codes[i], codes[j]}) not in transfer_edge_pairs:
                    missing_interchange_edges.append((station.station_name, codes[i], codes[j]))
    if missing_interchange_edges:
        problems.append(f"{len(missing_interchange_edges)} interchange station code-pairs have no transfer edge: {missing_interchange_edges}")

    # Connectivity: the whole graph should be one connected component —
    # a disconnected node/cluster usually means an authoring mistake.
    adjacency: dict[str, set[str]] = {}
    for e in data.edges:
        adjacency.setdefault(e.from_node, set()).add(e.to_node)
        adjacency.setdefault(e.to_node, set()).add(e.from_node)
    if node_codes:
        from collections import deque

        start = next(iter(node_codes))
        seen = {start}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            for nxt in adjacency.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        unreachable = node_codes - seen
        if unreachable:
            problems.append(f"{len(unreachable)} station codes are disconnected from the main graph: {sorted(unreachable)}")

    print(
        f"stations={len(data.stations)} edges={len(data.edges)} "
        f"interchanges={len(interchange_stations)} version={data.version}"
    )
    for p in problems:
        print(f"  PROBLEM: {p}")
    return not problems


def main() -> int:
    bus_ok = validate_bus()
    rail_ok = validate_rail()
    print(f"\nOverall: {'PASS' if bus_ok and rail_ok else 'FAIL'}")
    return 0 if (bus_ok and rail_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
