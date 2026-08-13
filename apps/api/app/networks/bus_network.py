"""Boarding-stop-specific bus corridor grouping and transfer queries.

Bus Coverage represents choices available *from the stops a resident can
actually board at*. It therefore compares ordered downstream stop sequences,
not whole service routes or unordered stop sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache

from app.adapters.transport_data.lta_bus import BusServiceInfo, LtaBusDataStore, ServiceDirectionKey
from app.engines.transport_config import PT_CONFIG


@dataclass(frozen=True, order=True)
class BoardingServiceOption:
    """One (service, direction, boarding-stop) choice and its ordered suffix."""

    service_key: ServiceDirectionKey
    boarding_stop_code: str
    downstream_stop_codes: tuple[str, ...]


@dataclass(frozen=True)
class CorridorInfo:
    corridor_id: str
    # Retained for existing evidence consumers; membership is now derived from
    # boarding options rather than a global service-level corridor cache.
    member_services: frozenset[ServiceDirectionKey]
    representative_destination: str
    member_options: frozenset[BoardingServiceOption] = field(default_factory=frozenset)


class _UnionFind:
    def __init__(self, items: list[BoardingServiceOption]) -> None:
        self._parent = {item: item for item in items}

    def find(self, item: BoardingServiceOption) -> BoardingServiceOption:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: BoardingServiceOption, b: BoardingServiceOption) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_a] = root_b


def _longest_common_subsequence_length(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    """Return the length of the ordered stop sequence shared by two options."""
    previous = [0] * (len(right) + 1)
    for left_stop in left:
        current = [0]
        for index, right_stop in enumerate(right, 1):
            current.append(previous[index - 1] + 1 if left_stop == right_stop else max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def downstream_similarity(left: BoardingServiceOption, right: BoardingServiceOption) -> float:
    """LCS divided by the shorter downstream sequence length.

    Sequence order matters. Reversed routes generally share only their
    boarding stop, while routes sharing a long ordered suffix score highly.
    """
    shorter = min(len(left.downstream_stop_codes), len(right.downstream_stop_codes))
    if not shorter:
        return 0.0
    return _longest_common_subsequence_length(left.downstream_stop_codes, right.downstream_stop_codes) / shorter


class BoardingCorridorContext:
    """Corridors for one listing's eligible boarding stops and transfers."""

    def __init__(
        self,
        corridor_by_option: dict[BoardingServiceOption, str],
        corridors: dict[str, CorridorInfo],
        direct_options: frozenset[BoardingServiceOption],
        direct_corridor_ids: set[str],
        one_transfer_corridor_ids: set[str],
    ) -> None:
        self._corridor_by_option = corridor_by_option
        self._corridors = corridors
        self._direct_options = direct_options
        self._direct_corridor_ids = direct_corridor_ids
        self._one_transfer_corridor_ids = one_transfer_corridor_ids

    def corridor_for_option(self, option: BoardingServiceOption) -> str | None:
        return self._corridor_by_option.get(option)

    def corridor_info(self, corridor_id: str) -> CorridorInfo | None:
        return self._corridors.get(corridor_id)

    def direct_corridor_ids(self) -> set[str]:
        return set(self._direct_corridor_ids)

    def one_transfer_corridor_ids(self) -> set[str]:
        return set(self._one_transfer_corridor_ids)

    def boarding_stops_for_corridor(self, corridor_id: str, *, direct_only: bool) -> set[str]:
        info = self.corridor_info(corridor_id)
        if info is None:
            return set()
        return {
            option.boarding_stop_code
            for option in info.member_options
            if not direct_only or option in self._direct_options
        }

    def service_keys_at_boarding_stop(
        self, corridor_id: str, stop_code: str, *, direct_only: bool
    ) -> set[ServiceDirectionKey]:
        info = self.corridor_info(corridor_id)
        if info is None:
            return set()
        return {
            option.service_key
            for option in info.member_options
            if option.boarding_stop_code == stop_code and (not direct_only or option in self._direct_options)
        }


class BusNetwork:
    """Builds deterministic corridor contexts from LTA service-direction data."""

    def __init__(self, overlap_threshold: float | None = None) -> None:
        # The normal runtime path receives PT_CONFIG's value through
        # get_bus_network(); a direct caller can still provide a test override.
        self.overlap_threshold = (
            PT_CONFIG.corridor_overlap_threshold if overlap_threshold is None else overlap_threshold
        )

    @staticmethod
    def downstream_option(key: ServiceDirectionKey, boarding_stop_code: str) -> BoardingServiceOption | None:
        route_stops = LtaBusDataStore.route_stops(key)
        start_index = next(
            (index for index, row in enumerate(route_stops) if row.bus_stop_code == boarding_stop_code), None
        )
        if start_index is None:
            return None
        return BoardingServiceOption(
            service_key=key,
            boarding_stop_code=boarding_stop_code,
            downstream_stop_codes=tuple(row.bus_stop_code for row in route_stops[start_index:]),
        )

    @staticmethod
    def _destination_label(option: BoardingServiceOption) -> str:
        info: BusServiceInfo | None = LtaBusDataStore.service_info(option.service_key)
        if info is None:
            return f"Service {option.service_key[0]}"
        return f"Service {info.service_no} towards {info.destination_code or info.origin_code}"

    def _group_options(
        self, options: set[BoardingServiceOption], *, kind: str
    ) -> tuple[dict[BoardingServiceOption, str], dict[str, CorridorInfo]]:
        ordered_options = sorted(options)
        union_find = _UnionFind(ordered_options)
        by_stop: dict[str, list[BoardingServiceOption]] = {}
        for option in ordered_options:
            for stop_code in set(option.downstream_stop_codes):
                by_stop.setdefault(stop_code, []).append(option)

        compared: set[tuple[BoardingServiceOption, BoardingServiceOption]] = set()
        for candidates in by_stop.values():
            for index, left in enumerate(candidates):
                for right in candidates[index + 1 :]:
                    pair = (left, right) if left < right else (right, left)
                    if pair in compared:
                        continue
                    compared.add(pair)
                    if downstream_similarity(left, right) >= self.overlap_threshold:
                        union_find.union(left, right)

        groups: dict[BoardingServiceOption, set[BoardingServiceOption]] = {}
        for option in ordered_options:
            groups.setdefault(union_find.find(option), set()).add(option)

        corridor_by_option: dict[BoardingServiceOption, str] = {}
        corridors: dict[str, CorridorInfo] = {}
        for root, members in groups.items():
            corridor_id = (
                f"CORRIDOR:{kind}:{root.service_key[0]}:{root.service_key[1]}:{root.boarding_stop_code}"
            )
            info = CorridorInfo(
                corridor_id=corridor_id,
                member_services=frozenset(option.service_key for option in members),
                representative_destination=self._destination_label(root),
                member_options=frozenset(members),
            )
            corridors[corridor_id] = info
            for member in members:
                corridor_by_option[member] = corridor_id
        return corridor_by_option, corridors

    @staticmethod
    def _same_stop_transfer_options(direct_option: BoardingServiceOption) -> set[BoardingServiceOption]:
        """Return V1 transfer options boardable at Bus A's exact alighting stop.

        LTA bus fixture data currently has no reliable bus-interchange/transfer-
        complex relationship. Consequently, V1 permits only an identical
        ``BusStopCode``: a second service at a different, merely nearby stop
        is never treated as transferable without routed walking evidence.
        """
        options: set[BoardingServiceOption] = set()
        # Excluding index zero ensures a transfer happens after the resident
        # boards Bus A, never at the listing's original boarding stop.
        for alighting_stop_code in direct_option.downstream_stop_codes[1:]:
            for key in LtaBusDataStore.services_by_stop(alighting_stop_code):
                option = BusNetwork.downstream_option(key, alighting_stop_code)
                if option is not None:
                    options.add(option)
        return options

    def corridors_for_boarding_stops(
        self, stop_codes: set[str], *, include_one_transfer: bool = True
    ) -> BoardingCorridorContext:
        direct_options = {
            option
            for stop_code in stop_codes
            for key in LtaBusDataStore.services_by_stop(stop_code)
            if (option := self.downstream_option(key, stop_code)) is not None
        }
        transfer_options: set[BoardingServiceOption] = set()
        if include_one_transfer:
            for direct_option in direct_options:
                transfer_options |= self._same_stop_transfer_options(direct_option)
        direct_by_option, direct_corridors = self._group_options(direct_options, kind="DIRECT")
        # Discard only transfer options that themselves duplicate a direct
        # choice. This happens before grouping, so a duplicate option cannot
        # pull a genuinely new transfer service into an excluded group.
        novel_transfer_options = {
            transfer_option
            for transfer_option in transfer_options
            if not any(
                downstream_similarity(transfer_option, direct_option) >= self.overlap_threshold
                for direct_option in direct_options
            )
        }
        # Group transfer options separately so they cannot bridge or merge two
        # otherwise distinct direct corridors.
        transfer_by_option, transfer_corridors = self._group_options(novel_transfer_options, kind="TRANSFER")
        retained_transfer_ids = set(transfer_corridors)
        corridor_by_option = {
            **direct_by_option,
            **transfer_by_option,
        }
        corridors = {
            **direct_corridors,
            **transfer_corridors,
        }
        return BoardingCorridorContext(
            corridor_by_option,
            corridors,
            frozenset(direct_options),
            set(direct_corridors),
            retained_transfer_ids,
        )


@cache
def get_bus_network(overlap_threshold: float = PT_CONFIG.corridor_overlap_threshold) -> BusNetwork:
    """Return a network configured with the caller's active PT threshold."""
    return BusNetwork(overlap_threshold=overlap_threshold)
