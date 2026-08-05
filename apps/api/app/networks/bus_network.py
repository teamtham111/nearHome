"""Bus corridor grouping/deduplication and coverage/resilience queries.

Per the spec: bus coverage must measure genuinely different directions,
destinations and travel corridors — not a raw bus-stop or bus-service count.
This module groups near-duplicate service-directions ("corridors") using a
deterministic stop-sequence overlap heuristic, then exposes direct-coverage
and practical-one-transfer queries built on top of that grouping.

This is *structural* network analysis (Part 8) — it never calls the routing
provider and never claims a live/real-time result. Component engines pair
this with routed evidence (actual walking access) separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.adapters.transport_data.lta_bus import BusServiceInfo, LtaBusDataStore, ServiceDirectionKey

# Two service-directions sharing at least this fraction of stops (relative to
# the shorter route) are treated as the same corridor rather than double
# counted as independent coverage. Configurable, documented heuristic — not
# a geometry/polyline analysis.
CORRIDOR_OVERLAP_THRESHOLD = 0.70


@dataclass(frozen=True)
class CorridorInfo:
    corridor_id: str
    member_services: frozenset[ServiceDirectionKey]
    representative_destination: str


class _UnionFind:
    def __init__(self, items: list[ServiceDirectionKey]) -> None:
        self._parent = {item: item for item in items}

    def find(self, item: ServiceDirectionKey) -> ServiceDirectionKey:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: ServiceDirectionKey, b: ServiceDirectionKey) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


class BusNetwork:
    """Wraps `LtaBusDataStore` with corridor-level (deduplicated) queries."""

    def __init__(self, overlap_threshold: float = CORRIDOR_OVERLAP_THRESHOLD) -> None:
        self.overlap_threshold = overlap_threshold
        self._corridor_by_service: dict[ServiceDirectionKey, str] | None = None
        self._corridors: dict[str, CorridorInfo] | None = None

    def _stop_set(self, key: ServiceDirectionKey) -> frozenset[str]:
        return frozenset(r.bus_stop_code for r in LtaBusDataStore.route_stops(key))

    def _build_corridors(self) -> None:
        if self._corridors is not None:
            return

        all_keys = LtaBusDataStore.all_service_directions()
        stop_sets = {key: self._stop_set(key) for key in all_keys}
        uf = _UnionFind(all_keys)

        # Only compare service-directions that plausibly overlap (share at
        # least one stop) — avoids an O(n^2) full cross-product blow-up on
        # ~800 service-directions.
        by_first_stop: dict[str, list[ServiceDirectionKey]] = {}
        for key, stops in stop_sets.items():
            for stop in stops:
                by_first_stop.setdefault(stop, []).append(key)

        compared: set[frozenset[ServiceDirectionKey]] = set()
        for candidates in by_first_stop.values():
            for i in range(len(candidates)):
                for j in range(i + 1, len(candidates)):
                    a, b = candidates[i], candidates[j]
                    pair = frozenset({a, b})
                    if pair in compared:
                        continue
                    compared.add(pair)
                    stops_a, stops_b = stop_sets[a], stop_sets[b]
                    shorter = min(len(stops_a), len(stops_b))
                    if shorter == 0:
                        continue
                    overlap = len(stops_a & stops_b) / shorter
                    if overlap >= self.overlap_threshold:
                        uf.union(a, b)

        groups: dict[ServiceDirectionKey, set[ServiceDirectionKey]] = {}
        for key in all_keys:
            root = uf.find(key)
            groups.setdefault(root, set()).add(key)

        corridor_by_service: dict[ServiceDirectionKey, str] = {}
        corridors: dict[str, CorridorInfo] = {}
        for root, members in groups.items():
            corridor_id = f"CORRIDOR:{root[0]}:{root[1]}"
            representative = self._destination_label(root)
            info = CorridorInfo(
                corridor_id=corridor_id, member_services=frozenset(members), representative_destination=representative
            )
            corridors[corridor_id] = info
            for member in members:
                corridor_by_service[member] = corridor_id

        self._corridor_by_service = corridor_by_service
        self._corridors = corridors

    @staticmethod
    def _destination_label(key: ServiceDirectionKey) -> str:
        info: BusServiceInfo | None = LtaBusDataStore.service_info(key)
        if info is None:
            return f"Service {key[0]}"
        return f"Service {info.service_no} towards {info.destination_code or info.origin_code}"

    def corridor_for(self, key: ServiceDirectionKey) -> str | None:
        self._build_corridors()
        assert self._corridor_by_service is not None
        return self._corridor_by_service.get(key)

    def corridor_info(self, corridor_id: str) -> CorridorInfo | None:
        self._build_corridors()
        assert self._corridors is not None
        return self._corridors.get(corridor_id)

    def direct_corridors_for_stops(self, stop_codes: set[str]) -> set[str]:
        """Deduplicated corridors directly reachable from the given (routed-
        accessible) bus stops."""
        self._build_corridors()
        corridor_ids: set[str] = set()
        for stop_code in stop_codes:
            for key in LtaBusDataStore.services_by_stop(stop_code):
                corridor_id = self.corridor_for(key)
                if corridor_id:
                    corridor_ids.add(corridor_id)
        return corridor_ids

    def one_transfer_corridors(self, direct_stop_codes: set[str], direct_corridor_ids: set[str]) -> set[str]:
        """Corridors reachable by staying on a directly-accessible
        service-direction to any of its stops, then picking up a *different*
        corridor at that stop. Structural (network-graph) reach, not a
        routed/timed transfer — see module docstring."""
        self._build_corridors()
        new_corridors: set[str] = set()
        visited_services: set[ServiceDirectionKey] = set()
        for stop_code in direct_stop_codes:
            visited_services |= LtaBusDataStore.services_by_stop(stop_code)

        for key in visited_services:
            for route_stop in LtaBusDataStore.route_stops(key):
                for other_key in LtaBusDataStore.services_by_stop(route_stop.bus_stop_code):
                    other_corridor = self.corridor_for(other_key)
                    if other_corridor and other_corridor not in direct_corridor_ids:
                        new_corridors.add(other_corridor)
        return new_corridors


@lru_cache(maxsize=1)
def get_bus_network() -> BusNetwork:
    return BusNetwork()
