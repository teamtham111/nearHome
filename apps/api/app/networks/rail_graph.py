"""Hand-rolled Dijkstra over the curated rail graph (no networkx dependency).

Weights are `estimated_minutes` (ride ~2-2.5 min, transfer 5-6 min) from
`data_pipeline/build_rail_graph.py`'s output — a structural approximation
for reachability/transfer-count analysis, not a live train-timetable
duration. Callers that need an actual travel time must use the
`RoutingProvider` (real Google Transit routing), never this graph's minutes.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from functools import lru_cache

from app.adapters.reference_data import haversine_m
from app.adapters.transport_data.rail_data import RailDataStore, RailGraphData, RailStation


@dataclass(frozen=True)
class RailPathResult:
    total_minutes: float
    node_path: list[str]
    line_path: list[str]
    transfers: int


class RailGraph:
    def __init__(self, data: RailGraphData | None = None) -> None:
        self._data = data or RailDataStore.load()
        self._adjacency: dict[str, list[tuple[str, float, str, str]]] = {}
        for edge in self._data.edges:
            self._adjacency.setdefault(edge.from_node, []).append(
                (edge.to_node, edge.estimated_minutes, edge.edge_type, edge.line)
            )
            self._adjacency.setdefault(edge.to_node, []).append(
                (edge.from_node, edge.estimated_minutes, edge.edge_type, edge.line)
            )
        self._code_to_name = self._data.code_to_station_name()

    @property
    def is_loaded(self) -> bool:
        return bool(self._data.stations) and bool(self._data.edges)

    def all_codes(self) -> list[str]:
        return list(self._code_to_name.keys())

    def station_name_for_code(self, code: str) -> str | None:
        return self._code_to_name.get(code)

    def station_by_code(self, code: str) -> RailStation | None:
        name = self._code_to_name.get(code)
        if name is None:
            return None
        return self._data.station_by_name(name)

    def station_by_name(self, name: str) -> RailStation | None:
        return self._data.station_by_name(name)

    def nearby_station_codes(
        self, latitude: float, longitude: float, max_distance_m: float
    ) -> list[tuple[str, float]]:
        """Haversine pre-filter ONLY — returns candidate codes within
        `max_distance_m` straight-line distance, sorted nearest-first. Never
        use the returned distance as a walking time; that requires a real
        RoutingProvider call on the shortlist this returns."""
        candidates: list[tuple[str, float]] = []
        for station in self._data.stations:
            if not station.active or station.latitude is None or station.longitude is None:
                continue
            distance = haversine_m(latitude, longitude, station.latitude, station.longitude)
            if distance <= max_distance_m:
                for code in station.codes:
                    candidates.append((code, distance))
        candidates.sort(key=lambda c: c[1])
        return candidates

    def closest_physical_station(self, latitude: float, longitude: float) -> tuple[RailStation, float] | None:
        """Return the nearest active physical station by straight-line distance.

        This is used for the MRT Reach component's network-origin selection.
        It deliberately scans all active stations instead of applying Access's
        practical-walking pre-filter; walking feasibility is a separate
        Access concern and must not suppress a rail-network reach score.
        """
        candidates = [
            (station, haversine_m(latitude, longitude, station.latitude, station.longitude))
            for station in self._data.stations
            if station.active and station.latitude is not None and station.longitude is not None
        ]
        return min(candidates, key=lambda item: item[1]) if candidates else None

    def shortest_path(self, origin_code: str, destination_code: str) -> RailPathResult | None:
        """Dijkstra over ride+transfer edges. Because transfer edges cost
        more than ride edges, minimum-time paths naturally minimise
        transfers too, without a separate min-transfer search."""
        if origin_code == destination_code:
            return RailPathResult(0.0, [origin_code], [], 0)
        if origin_code not in self._adjacency or destination_code not in self._adjacency:
            return None

        distances: dict[str, float] = {origin_code: 0.0}
        previous: dict[str, tuple[str, str, str]] = {}
        visited: set[str] = set()
        heap: list[tuple[float, str]] = [(0.0, origin_code)]

        while heap:
            dist, node = heapq.heappop(heap)
            if node in visited:
                continue
            visited.add(node)
            if node == destination_code:
                break
            for neighbor, weight, edge_type, line in self._adjacency.get(node, []):
                new_dist = dist + weight
                if new_dist < distances.get(neighbor, float("inf")):
                    distances[neighbor] = new_dist
                    previous[neighbor] = (node, edge_type, line)
                    heapq.heappush(heap, (new_dist, neighbor))

        if destination_code not in distances:
            return None

        node_path = [destination_code]
        line_path: list[str] = []
        transfers = 0
        cur = destination_code
        while cur in previous:
            prev_node, edge_type, line = previous[cur]
            node_path.append(prev_node)
            line_path.append(line)
            if edge_type == "transfer":
                transfers += 1
            cur = prev_node
        node_path.reverse()
        line_path.reverse()
        return RailPathResult(
            total_minutes=round(distances[destination_code], 1),
            node_path=node_path,
            line_path=line_path,
            transfers=transfers,
        )

    def reachable_within(self, origin_code: str, max_minutes: float) -> dict[str, float]:
        """Dijkstra frontier expansion — all nodes reachable within `max_minutes`."""
        if origin_code not in self._adjacency:
            return {}
        distances: dict[str, float] = {origin_code: 0.0}
        visited: set[str] = set()
        heap: list[tuple[float, str]] = [(0.0, origin_code)]
        while heap:
            dist, node = heapq.heappop(heap)
            if node in visited or dist > max_minutes:
                continue
            visited.add(node)
            for neighbor, weight, _edge_type, _line in self._adjacency.get(node, []):
                new_dist = dist + weight
                if new_dist <= max_minutes and new_dist < distances.get(neighbor, float("inf")):
                    distances[neighbor] = new_dist
                    heapq.heappush(heap, (new_dist, neighbor))
        return distances

    def reachable_physical_stations(
        self, origin_codes: list[str] | tuple[str, ...], max_minutes: float
    ) -> dict[str, RailPathResult]:
        """Return the shortest structural path to each physical station once.

        The graph stores one node per station-line code, so this method is the
        single place where line-code reach is collapsed back to physical
        stations. The shortest path's transfer count is retained for mutually
        exclusive MRT reach buckets.
        """
        result: dict[str, RailPathResult] = {}
        for station in self._data.stations:
            if not station.active:
                continue
            candidates = [
                path
                for origin in origin_codes
                for destination in station.codes
                if (path := self.shortest_path(origin, destination)) is not None
                and path.total_minutes <= max_minutes
            ]
            if candidates:
                result[station.station_name] = min(
                    candidates, key=lambda path: (path.total_minutes, path.transfers)
                )
        return result

    def lines_at(self, code: str) -> set[str]:
        name = self._code_to_name.get(code)
        if name is None:
            return set()
        station = self._data.station_by_name(name)
        return set(station.lines) if station else set()

    def lines_reachable_within_one_transfer(self, origin_code: str) -> set[str]:
        """Every line reachable from `origin_code` using at most one
        transfer — used for the route-resilience component (does this
        listing depend on a single line with no practical alternative?)."""
        lines = set(self.lines_at(origin_code))
        for neighbor, _weight, edge_type, line in self._adjacency.get(origin_code, []):
            if edge_type == "transfer":
                lines |= self.lines_at(neighbor)
            else:
                lines.add(line)
        return lines


@lru_cache(maxsize=1)
def get_rail_graph() -> RailGraph:
    return RailGraph()
