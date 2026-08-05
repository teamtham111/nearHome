"""MRT Reach from the geographically closest active MRT station.

Walking, feeder and waiting friction belong to Access. This component starts
at exactly one physical station selected from the listing coordinates and
scores mutually exclusive structural rail-reach buckets. The station is
selected independently of whether Access confirms a practical walking or
feeder path to it.
"""

from __future__ import annotations

from typing import Any

from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed
from app.engines.transport_config import PT_CONFIG, PublicTransportConfig
from app.networks.rail_graph import RailGraph, get_rail_graph


def _bucket_score(count: int, saturation: int) -> float:
    return min(100.0, count / saturation * 100.0) if saturation else 0.0


def _normalise_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accept new Access rail entries and legacy direct-walk evidence."""
    normalised: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.get("physical_station_id"):
            candidate = dict(entry)
        elif entry.get("access_point_type") == "mrt_station" or entry.get("station_name"):
            candidate = {
                "physical_station_id": entry.get("station_name"),
                "station_codes": entry.get("codes", []),
                "access_mode": "direct_walk",
                "generalised_access_cost": entry.get("walk_minutes", 0),
                "total_expected_minutes": entry.get("walk_minutes", 0),
            }
        else:
            continue
        station_id = str(candidate["physical_station_id"])
        previous = normalised.get(station_id)
        if previous is None or candidate.get("generalised_access_cost", float("inf")) < previous.get(
            "generalised_access_cost", float("inf")
        ):
            normalised[station_id] = candidate
    return sorted(
        normalised.values(),
        key=lambda item: (
            0 if item.get("is_primary_rail_entry") else 1,
            item.get("generalised_access_cost", 0),
            item["physical_station_id"],
        ),
    )


def compute_mrt_reach(
    practical_rail_entries: list[dict[str, Any]] | None = None,
    config: PublicTransportConfig = PT_CONFIG,
    rail_graph: RailGraph | None = None,
    *,
    origin_latitude: float,
    origin_longitude: float,
) -> ComponentResult:
    weight = config.weight_mrt_reach
    graph = rail_graph or get_rail_graph()
    if not graph.is_loaded:
        return not_assessed(
            "mrt_reach",
            weight,
            "The curated rail network graph is unavailable.",
            limitations=["Rail graph fixtures failed to load — run data_pipeline/build_rail_graph.py."],
        )

    access_entries = _normalise_entries(practical_rail_entries or [])
    nearest = graph.closest_physical_station(origin_latitude, origin_longitude)
    if nearest is None:
        return not_assessed(
            "mrt_reach",
            weight,
            "No active MRT station with valid coordinates is available in the rail graph.",
        )
    nearest_station, nearest_distance_m = nearest
    primary_id = nearest_station.station_name
    origin_codes = list(nearest_station.codes)
    station = nearest_station
    selection_source = "geographic_nearest"

    if not origin_codes or station is None:
        return not_assessed(
            "mrt_reach",
            weight,
            "The selected MRT station has no usable station-line code in the rail graph.",
        )

    reachable_30 = graph.reachable_physical_stations(origin_codes, config.reachable_within_minutes_short)
    reachable_45 = graph.reachable_physical_stations(origin_codes, config.reachable_within_minutes_long)
    zero = {name for name, path in reachable_30.items() if path.transfers == 0}
    one = {name for name, path in reachable_30.items() if path.transfers == 1} - zero
    multi = {name for name, path in reachable_30.items() if path.transfers >= 2} - zero - one
    within_30 = zero | one | multi
    extended = set(reachable_45) - within_30

    def station_summary(name: str, path: Any, bucket: str) -> dict[str, Any]:
        reachable_station = graph.station_by_name(name)
        return {
            "station_name": name,
            "station_codes": list(reachable_station.codes) if reachable_station else [],
            "lines": sorted(reachable_station.lines) if reachable_station else [],
            "bucket": bucket,
            "estimated_minutes": path.total_minutes,
            "rail_transfers": path.transfers,
        }

    reachable_station_summaries = {
        "zero_transfer": [
            station_summary(name, path, "zero_transfer")
            for name, path in sorted(reachable_30.items())
            if name in zero
        ],
        "one_transfer": [
            station_summary(name, path, "one_transfer")
            for name, path in sorted(reachable_30.items())
            if name in one
        ],
        "additional_transfers": [
            station_summary(name, path, "additional_transfers")
            for name, path in sorted(reachable_30.items())
            if name in multi
        ],
        "extended": [
            station_summary(name, path, "extended")
            for name, path in sorted(reachable_45.items())
            if name in extended
        ],
    }

    direct_lines = set(station.lines)
    additional_lines: set[str] = set()
    for code in origin_codes:
        additional_lines |= graph.lines_reachable_within_one_transfer(code)
    additional_lines -= direct_lines

    score = round(
        _bucket_score(len(zero), config.mrt_zero_transfer_saturation) * 0.35
        + _bucket_score(len(one), config.mrt_one_transfer_saturation) * 0.35
        + _bucket_score(len(multi), config.mrt_multi_transfer_saturation) * 0.10
        + _bucket_score(len(extended), config.mrt_extended_saturation) * 0.20,
        1,
    )
    alternative_stations = [
        entry for entry in access_entries if entry.get("physical_station_id") != primary_id
    ]
    evidence = {
        "primary_physical_station_id": primary_id,
        "station_codes": list(station.codes),
        "selection_method": selection_source,
        "closest_station_distance_metres": round(nearest_distance_m, 1) if nearest_distance_m is not None else None,
        "access_confirmed_practical_entry": any(
            entry.get("physical_station_id") == primary_id for entry in access_entries
        ),
        "direct_lines": sorted(direct_lines),
        "primary_station_lines": sorted(station.lines),
        "primary_station_name": station.station_name,
        "is_interchange": station.is_interchange,
        "additional_lines_with_one_transfer": sorted(additional_lines),
        "zero_transfer_30": len(zero),
        "one_transfer_30_incremental": len(one),
        "multi_transfer_30_incremental": len(multi),
        "extended_31_to_45_incremental": len(extended),
        "zero_transfer_station_ids": sorted(zero),
        "one_transfer_station_ids": sorted(one),
        "multi_transfer_station_ids": sorted(multi),
        "extended_station_ids": sorted(extended),
        "reachable_station_summaries": reachable_station_summaries,
        "alternative_practical_stations": alternative_stations,
    }
    return ComponentResult(
        name="mrt_reach",
        value={
            "primary_physical_station_id": primary_id,
            "closest_station_distance_metres": round(nearest_distance_m, 1)
            if nearest_distance_m is not None
            else None,
            "direct_lines": len(direct_lines),
            "is_interchange": station.is_interchange,
            "additional_lines": len(additional_lines),
            "zero_transfer_30": len(zero),
            "one_transfer_30_incremental": len(one),
            "multi_transfer_30_incremental": len(multi),
            "extended_31_to_45_incremental": len(extended),
        },
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"{primary_id} is the geographically closest MRT station; {len(zero)} zero-transfer, "
            f"{len(one)} one-transfer and {len(extended)} extended physical stations are counted once."
        ),
        strengths=[f"Direct rail lines: {', '.join(sorted(direct_lines)) or 'none'}."]
        + (
            [
                f"{len(additional_lines)} additional rail line(s) beyond the direct lines "
                "are reachable with one transfer."
            ]
            if additional_lines
            else []
        ),
        limitations=[
            "Reach uses approximate structural graph minutes, not live train timetables.",
            "Walking, feeder, waiting and station-entry minutes are excluded because they belong to Access.",
        ],
        evidence=[evidence],
        source="Nearest active MRT station coordinates + curated rail graph",
        provenance=Provenance.CURATED_REFERENCE_DATA,
        confidence="high",
    )
