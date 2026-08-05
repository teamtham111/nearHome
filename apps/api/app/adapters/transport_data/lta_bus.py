"""Joined LTA bus reference data — BusStops + BusRoutes + BusServices.

This is the single place the join described in the transport-data spec
happens:

    BusStops.BusStopCode = BusRoutes.BusStopCode

Building `services_by_stop`, `route_stops_by_service_direction`, and
`service_frequency_by_period` here (in-memory, from the ingested JSON
fixtures) keeps the same join logic shared between the runtime engines and
the `data_pipeline/build_bus_indexes.py` / `validate_transport_data.py` CLI
scripts, so there is exactly one implementation to keep correct.

Service direction is preserved everywhere: service "117" direction 1 and
service "117" direction 2 are different keys, never merged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[5]
FIXTURES = ROOT / "data_pipeline" / "fixtures"

ServiceDirectionKey = tuple[str, int]


@dataclass(frozen=True)
class BusRouteStop:
    service_no: str
    direction: int
    stop_sequence: int
    bus_stop_code: str
    distance_km: float | None


@dataclass(frozen=True)
class FrequencyRange:
    """A scheduled dispatch-frequency *range* for one period — not an exact wait.

    Parsed from LTA's "8-12" (minutes) style strings. `midpoint_minutes` may
    be used internally for scoring, but user-facing text must say
    "approximately X-Y min", never "a bus arrives every Z minutes".
    """

    minimum_minutes: float
    maximum_minutes: float
    midpoint_minutes: float
    source_period: str

    def as_label(self) -> str:
        if self.minimum_minutes == self.maximum_minutes:
            return f"approximately {self.minimum_minutes:g} min"
        return f"approximately {self.minimum_minutes:g}-{self.maximum_minutes:g} min"


@dataclass(frozen=True)
class BusServiceInfo:
    service_no: str
    direction: int
    operator: str
    category: str
    origin_code: str
    destination_code: str
    loop_desc: str
    frequencies: dict[str, FrequencyRange]


_FREQ_RANGE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")
_FREQ_SINGLE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")
# LTA uses a bare "-" to mean "this service does not operate during this
# period" — that is a genuine, meaningful value, not a parse failure.
_NO_SERVICE_MARKERS = {"-", "", "n/a", "na", "nil"}


def is_no_service_marker(raw: str | None) -> bool:
    return raw is None or raw.strip().lower() in _NO_SERVICE_MARKERS


def parse_frequency_range(raw: str | None, period: str) -> FrequencyRange | None:
    """Parse "8-12" -> FrequencyRange(8, 12, 10, period).

    Also accepts a single number ("15") as a fixed (non-range) frequency.
    Returns None both for "no service this period" markers (e.g. "-") and
    for genuinely malformed values — callers that need to distinguish the
    two should check `is_no_service_marker()` first.
    """
    if is_no_service_marker(raw):
        return None
    assert raw is not None
    range_match = _FREQ_RANGE_PATTERN.match(raw)
    if range_match:
        lo, hi = float(range_match.group(1)), float(range_match.group(2))
    else:
        single_match = _FREQ_SINGLE_PATTERN.match(raw)
        if not single_match:
            return None
        lo = hi = float(single_match.group(1))
    if lo <= 0 and hi <= 0:
        return None
    return FrequencyRange(
        minimum_minutes=lo, maximum_minutes=hi, midpoint_minutes=round((lo + hi) / 2, 1), source_period=period
    )


@dataclass
class DataQualityReport:
    bus_stops_count: int
    bus_routes_rows: int
    bus_services_rows: int
    unique_service_directions_in_routes: int
    unique_service_directions_in_services: int
    stops_with_no_routes: int
    route_stops_referencing_unknown_stop_codes: int
    duplicate_stop_sequences: int
    malformed_frequency_values: int
    is_usable: bool
    problems: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "bus_stops_count": self.bus_stops_count,
            "bus_routes_rows": self.bus_routes_rows,
            "bus_services_rows": self.bus_services_rows,
            "unique_service_directions_in_routes": self.unique_service_directions_in_routes,
            "unique_service_directions_in_services": self.unique_service_directions_in_services,
            "stops_with_no_routes": self.stops_with_no_routes,
            "route_stops_referencing_unknown_stop_codes": self.route_stops_referencing_unknown_stop_codes,
            "duplicate_stop_sequences": self.duplicate_stop_sequences,
            "malformed_frequency_values": self.malformed_frequency_values,
            "is_usable": self.is_usable,
            "problems": self.problems,
        }


# Below this, the joined dataset is considered too sparse to trust — engines
# must report `not_assessed` for anything derived from bus-service data
# rather than silently proceeding on a broken import.
MIN_USABLE_STOPS_WITH_ROUTES_RATIO = 0.5
MIN_USABLE_SERVICE_DIRECTIONS = 100


class LtaBusDataStore:
    """Singleton-style loader + index builder for the joined bus dataset."""

    _stop_codes: set[str] | None = None
    _route_rows: list[BusRouteStop] | None = None
    _service_info: dict[ServiceDirectionKey, BusServiceInfo] | None = None
    _services_by_stop: dict[str, set[ServiceDirectionKey]] | None = None
    _route_stops_by_service_direction: dict[ServiceDirectionKey, list[BusRouteStop]] | None = None
    _quality_report: DataQualityReport | None = None

    @classmethod
    def _load_raw(cls) -> None:
        if cls._route_rows is not None:
            return

        stops_path = FIXTURES / "lta_bus_stops.json"
        routes_path = FIXTURES / "lta_bus_routes.json"
        services_path = FIXTURES / "lta_bus_services.json"

        stops_raw = json.loads(stops_path.read_text()) if stops_path.exists() else []
        routes_raw = json.loads(routes_path.read_text()) if routes_path.exists() else []
        services_raw = json.loads(services_path.read_text()) if services_path.exists() else []

        cls._stop_codes = {r["stop_code"] for r in stops_raw}

        route_rows = [
            BusRouteStop(
                service_no=r["service_no"],
                direction=int(r["direction"]),
                stop_sequence=int(r["stop_sequence"]),
                bus_stop_code=r["bus_stop_code"],
                distance_km=r.get("distance_km"),
            )
            for r in routes_raw
        ]
        cls._route_rows = route_rows

        service_info: dict[ServiceDirectionKey, BusServiceInfo] = {}
        for r in services_raw:
            key = (r["service_no"], int(r["direction"]))
            frequencies: dict[str, FrequencyRange] = {}
            for field_name, period in (
                ("am_peak_freq", "AM_PEAK"),
                ("am_offpeak_freq", "AM_OFFPEAK"),
                ("pm_peak_freq", "PM_PEAK"),
                ("pm_offpeak_freq", "PM_OFFPEAK"),
            ):
                parsed = parse_frequency_range(r.get(field_name), period)
                if parsed is not None:
                    frequencies[period] = parsed
            service_info[key] = BusServiceInfo(
                service_no=r["service_no"],
                direction=int(r["direction"]),
                operator=r.get("operator", ""),
                category=r.get("category", ""),
                origin_code=r.get("origin_code", ""),
                destination_code=r.get("destination_code", ""),
                loop_desc=r.get("loop_desc", ""),
                frequencies=frequencies,
            )
        cls._service_info = service_info

        services_by_stop: dict[str, set[ServiceDirectionKey]] = {}
        route_stops_by_service_direction: dict[ServiceDirectionKey, list[BusRouteStop]] = {}
        for row in route_rows:
            key = (row.service_no, row.direction)
            services_by_stop.setdefault(row.bus_stop_code, set()).add(key)
            route_stops_by_service_direction.setdefault(key, []).append(row)
        for stops in route_stops_by_service_direction.values():
            stops.sort(key=lambda r: r.stop_sequence)

        cls._services_by_stop = services_by_stop
        cls._route_stops_by_service_direction = route_stops_by_service_direction
        cls._quality_report = cls._build_quality_report(
            stops_raw, route_rows, services_raw, services_by_stop, route_stops_by_service_direction
        )

    @classmethod
    def _build_quality_report(
        cls,
        stops_raw: list[dict[str, Any]],
        route_rows: list[BusRouteStop],
        services_raw: list[dict[str, Any]],
        services_by_stop: dict[str, set[ServiceDirectionKey]],
        route_stops_by_service_direction: dict[ServiceDirectionKey, list[BusRouteStop]],
    ) -> DataQualityReport:
        problems: list[str] = []
        stop_codes = {r["stop_code"] for r in stops_raw}
        stops_with_no_routes = len([code for code in stop_codes if code not in services_by_stop])
        unknown_stop_refs = len({r.bus_stop_code for r in route_rows if r.bus_stop_code not in stop_codes})

        duplicate_sequences = 0
        for rows in route_stops_by_service_direction.values():
            seqs = [r.stop_sequence for r in rows]
            if len(seqs) != len(set(seqs)):
                duplicate_sequences += 1

        malformed_freq = 0
        for r in services_raw:
            for field_name, period in (
                ("am_peak_freq", "AM_PEAK"),
                ("am_offpeak_freq", "AM_OFFPEAK"),
                ("pm_peak_freq", "PM_PEAK"),
                ("pm_offpeak_freq", "PM_OFFPEAK"),
            ):
                raw_val = r.get(field_name)
                if not is_no_service_marker(raw_val) and parse_frequency_range(raw_val, period) is None:
                    malformed_freq += 1

        stops_with_routes_ratio = 1 - (stops_with_no_routes / len(stop_codes)) if stop_codes else 0.0
        unique_service_directions = len(route_stops_by_service_direction)

        if not stop_codes:
            problems.append("No bus stops loaded — lta_bus_stops.json is missing or empty.")
        if not route_rows:
            problems.append("No BusRoutes rows loaded — lta_bus_routes.json is missing or empty.")
        if not services_raw:
            problems.append("No BusServices rows loaded — lta_bus_services.json is missing or empty.")
        if stops_with_routes_ratio < MIN_USABLE_STOPS_WITH_ROUTES_RATIO:
            problems.append(
                f"Only {stops_with_routes_ratio:.0%} of bus stops have any matched route/service — "
                "below the minimum usable threshold."
            )
        if unique_service_directions < MIN_USABLE_SERVICE_DIRECTIONS:
            problems.append(
                f"Only {unique_service_directions} distinct service-directions found — implausibly low "
                "for an island-wide bus network."
            )
        if unknown_stop_refs:
            problems.append(f"{unknown_stop_refs} BusRoutes stop codes do not match any known BusStop.")
        if duplicate_sequences:
            problems.append(f"{duplicate_sequences} service-directions have duplicated stop sequence numbers.")

        return DataQualityReport(
            bus_stops_count=len(stop_codes),
            bus_routes_rows=len(route_rows),
            bus_services_rows=len(services_raw),
            unique_service_directions_in_routes=unique_service_directions,
            unique_service_directions_in_services=len({(r["service_no"], int(r["direction"])) for r in services_raw}),
            stops_with_no_routes=stops_with_no_routes,
            route_stops_referencing_unknown_stop_codes=unknown_stop_refs,
            duplicate_stop_sequences=duplicate_sequences,
            malformed_frequency_values=malformed_freq,
            is_usable=not problems,
            problems=problems,
        )

    @classmethod
    def quality_report(cls) -> DataQualityReport:
        cls._load_raw()
        assert cls._quality_report is not None
        return cls._quality_report

    @classmethod
    def is_usable(cls) -> bool:
        return cls.quality_report().is_usable

    @classmethod
    def services_by_stop(cls, stop_code: str) -> set[ServiceDirectionKey]:
        cls._load_raw()
        assert cls._services_by_stop is not None
        return cls._services_by_stop.get(stop_code, set())

    @classmethod
    def service_info(cls, key: ServiceDirectionKey) -> BusServiceInfo | None:
        cls._load_raw()
        assert cls._service_info is not None
        return cls._service_info.get(key)

    @classmethod
    def all_service_directions(cls) -> list[ServiceDirectionKey]:
        cls._load_raw()
        assert cls._route_stops_by_service_direction is not None
        return list(cls._route_stops_by_service_direction.keys())

    @classmethod
    def route_stops(cls, key: ServiceDirectionKey) -> list[BusRouteStop]:
        cls._load_raw()
        assert cls._route_stops_by_service_direction is not None
        return cls._route_stops_by_service_direction.get(key, [])

    @classmethod
    def reset_cache(cls) -> None:
        """Test helper — force a reload on next access."""
        cls._stop_codes = None
        cls._route_rows = None
        cls._service_info = None
        cls._services_by_stop = None
        cls._route_stops_by_service_direction = None
        cls._quality_report = None
