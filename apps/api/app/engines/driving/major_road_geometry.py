"""Deterministic SLA × Google route validation for Major Road entry.

The catalogue junction is only a routing hypothesis.  A candidate is accepted
only when Google's ordered driving polyline has a continuous, directionally
aligned section inside the corresponding official SLA Major Road buffer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.adapters.routing.base import RouteResult
from app.adapters.transport_data.major_road_network import MajorRoadEntryPoint, SlaMajorRoad
from app.engines.transport_config import DRIVING_CONFIG, DrivingConfig
from app.networks.route_overlap import decode_google_polyline

_METRES_PER_DEGREE_LATITUDE = 111_320.0


@dataclass(frozen=True)
class SustainedEntryValidation:
    valid: bool
    reason: str
    sustained_overlap_metres: float
    actual_access_latitude: float | None = None
    actual_access_longitude: float | None = None
    # Diagnostics, kept for accepted and rejected candidates alike.
    route_end_shortfall_metres: float | None = None
    trailing_overlap_metres: float = 0.0
    closest_approach_metres: float | None = None
    route_points: int = 0


def validate_sustained_major_road_entry(
    route: RouteResult,
    road: SlaMajorRoad,
    candidate: MajorRoadEntryPoint,
    config: DrivingConfig = DRIVING_CONFIG,
) -> SustainedEntryValidation:
    """Reject crossings/touches; accept a continuous aligned route section."""
    points = decode_google_polyline(route.encoded_polyline)
    if len(points) < 2:
        return SustainedEntryValidation(False, "missing_google_route_polyline", 0.0, route_points=len(points))

    qualifying: list[tuple[tuple[float, float], float]] = []
    best_run: list[tuple[tuple[float, float], float]] = []
    closest_approach: float | None = None
    for start, end in zip(points, points[1:], strict=False):
        distance = _distance_metres(start, end)
        parts = max(1, math.ceil(distance / config.major_road_entry_sample_spacing_m))
        for index in range(parts):
            left = _interpolate(start, end, index / parts)
            right = _interpolate(start, end, (index + 1) / parts)
            midpoint = _interpolate(left, right, 0.5)
            segment_distance = _distance_metres(left, right)
            aligned, gap = _aligned_with_sla(left, right, midpoint, road, config)
            if gap is not None and (closest_approach is None or gap < closest_approach):
                closest_approach = gap
            if aligned:
                qualifying.append((left, segment_distance))
                if sum(length for _point, length in qualifying) > sum(length for _point, length in best_run):
                    best_run = list(qualifying)
            else:
                qualifying = []

    sustained = round(sum(length for _point, length in best_run), 1)
    # A route that is still on the road when it ends (the run in progress at
    # the final sample) but stops short of the catalogue target is judged on
    # that trailing run plus the unrouted gap to the target. The routing
    # engine snaps the destination to the nearest routable point, so routes
    # routinely end tens of metres before the target, which lies on the road.
    trailing = round(sum(length for _point, length in qualifying), 1)
    shortfall = round(_distance_metres(points[-1], candidate.routing_coordinate), 1)
    approach = None if closest_approach is None else round(closest_approach, 1)

    def diagnostics(**extra: object) -> dict[str, object]:
        return {
            "route_end_shortfall_metres": shortfall,
            "trailing_overlap_metres": trailing,
            "closest_approach_metres": approach,
            "route_points": len(points),
            **extra,
        }

    if sustained >= config.major_road_min_sustained_overlap_m:
        entry = best_run[0][0]
        return SustainedEntryValidation(
            True, "sustained_aligned_sla_overlap", sustained, entry[0], entry[1], **diagnostics()  # type: ignore[arg-type]
        )
    end_gap = _distance_to_road_metres(points[-1], road)
    if (
        qualifying
        and trailing >= config.major_road_min_trailing_overlap_m
        and shortfall <= config.major_road_max_route_end_shortfall_m
        and trailing + shortfall >= config.major_road_min_sustained_overlap_m
        and end_gap is not None
        and end_gap <= config.major_road_end_max_road_distance_m
        and _gap_runs_along_road(points[-1], candidate.routing_coordinate, road, config)
    ):
        entry = qualifying[0][0]
        return SustainedEntryValidation(
            True,
            "aligned_overlap_reaches_target_within_end_shortfall",
            trailing,
            entry[0],
            entry[1],
            **diagnostics(),  # type: ignore[arg-type]
        )
    return SustainedEntryValidation(
        False, "no_sustained_sla_major_road_overlap", sustained, **diagnostics()  # type: ignore[arg-type]
    )


def _distance_to_road_metres(point: tuple[float, float], road: SlaMajorRoad) -> float | None:
    distances = [
        _point_segment_distance(point, (left[1], left[0]), (right[1], right[0]))
        for line in road.lines
        for left, right in zip(line, line[1:], strict=False)
    ]
    return min(distances) if distances else None


def _gap_runs_along_road(
    end: tuple[float, float], target: tuple[float, float], road: SlaMajorRoad, config: DrivingConfig
) -> bool:
    """Every sample of the straight gap from the route end to the target is on the road."""
    distance = _distance_metres(end, target)
    if distance <= 0:
        return True
    parts = max(1, math.ceil(distance / config.major_road_entry_sample_spacing_m))
    for index in range(parts):
        left = _interpolate(end, target, index / parts)
        right = _interpolate(end, target, (index + 1) / parts)
        # A sub-sample gap has no meaningful heading: judge it on position only.
        if _distance_metres(left, right) < 1.0:
            gap = _distance_to_road_metres(_interpolate(left, right, 0.5), road)
            if gap is None or gap > config.major_road_sustained_entry_buffer_m:
                return False
            continue
        aligned, _gap = _aligned_with_sla(left, right, _interpolate(left, right, 0.5), road, config)
        if not aligned:
            return False
    return True


def _aligned_with_sla(
    start: tuple[float, float],
    end: tuple[float, float],
    midpoint: tuple[float, float],
    road: SlaMajorRoad,
    config: DrivingConfig,
) -> tuple[bool, float | None]:
    """Return (aligned within buffer, distance to the nearest SLA line)."""
    route_heading = _bearing(start, end)
    closest: tuple[float, float] | None = None
    for line in road.lines:
        for left, right in zip(line, line[1:], strict=False):
            # SLA is stored longitude/latitude; route geometry is latitude/longitude.
            distance = _point_segment_distance(midpoint, (left[1], left[0]), (right[1], right[0]))
            if closest is None or distance < closest[0]:
                closest = distance, _bearing((left[1], left[0]), (right[1], right[0]))
    if closest is None:
        return False, None
    if closest[0] > config.major_road_sustained_entry_buffer_m:
        return False, closest[0]
    # SLA line orientation is not a traffic-direction guarantee, hence either
    # alignment direction is valid; the topology catalogue carries direction.
    difference = abs((route_heading - closest[1] + 180) % 360 - 180)
    difference = min(difference, 180 - difference)
    return difference <= config.major_road_max_alignment_difference_degrees, closest[0]


def _interpolate(start: tuple[float, float], end: tuple[float, float], fraction: float) -> tuple[float, float]:
    return start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction


def _xy(point: tuple[float, float], reference_latitude: float) -> tuple[float, float]:
    return point[1] * _METRES_PER_DEGREE_LATITUDE * math.cos(math.radians(reference_latitude)), point[
        0
    ] * _METRES_PER_DEGREE_LATITUDE


def _distance_metres(start: tuple[float, float], end: tuple[float, float]) -> float:
    reference = (start[0] + end[0]) / 2
    sx, sy = _xy(start, reference)
    ex, ey = _xy(end, reference)
    return math.hypot(ex - sx, ey - sy)


def _point_segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    reference = (point[0] + start[0] + end[0]) / 3
    px, py = _xy(point, reference)
    sx, sy = _xy(start, reference)
    ex, ey = _xy(end, reference)
    dx, dy = ex - sx, ey - sy
    if dx == dy == 0:
        return math.hypot(px - sx, py - sy)
    fraction = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (sx + fraction * dx), py - (sy + fraction * dy))


def _bearing(start: tuple[float, float], end: tuple[float, float]) -> float:
    return math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 360
