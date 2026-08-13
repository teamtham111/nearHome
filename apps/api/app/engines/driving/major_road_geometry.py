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


def validate_sustained_major_road_entry(
    route: RouteResult,
    road: SlaMajorRoad,
    candidate: MajorRoadEntryPoint,
    config: DrivingConfig = DRIVING_CONFIG,
) -> SustainedEntryValidation:
    """Reject crossings/touches; accept a continuous aligned route section."""
    points = decode_google_polyline(route.encoded_polyline)
    if len(points) < 2:
        return SustainedEntryValidation(False, "missing_google_route_polyline", 0.0)

    qualifying: list[tuple[tuple[float, float], float]] = []
    best_run: list[tuple[tuple[float, float], float]] = []
    for start, end in zip(points, points[1:], strict=False):
        distance = _distance_metres(start, end)
        parts = max(1, math.ceil(distance / config.major_road_entry_sample_spacing_m))
        for index in range(parts):
            left = _interpolate(start, end, index / parts)
            right = _interpolate(start, end, (index + 1) / parts)
            midpoint = _interpolate(left, right, 0.5)
            segment_distance = _distance_metres(left, right)
            if _aligned_with_sla(left, right, midpoint, road, config):
                qualifying.append((left, segment_distance))
                if sum(length for _point, length in qualifying) > sum(length for _point, length in best_run):
                    best_run = list(qualifying)
            else:
                qualifying = []

    sustained = round(sum(length for _point, length in best_run), 1)
    if sustained < config.major_road_min_sustained_overlap_m:
        return SustainedEntryValidation(False, "no_sustained_sla_major_road_overlap", sustained)
    entry = best_run[0][0]
    return SustainedEntryValidation(True, "sustained_aligned_sla_overlap", sustained, entry[0], entry[1])


def _aligned_with_sla(
    start: tuple[float, float],
    end: tuple[float, float],
    midpoint: tuple[float, float],
    road: SlaMajorRoad,
    config: DrivingConfig,
) -> bool:
    route_heading = _bearing(start, end)
    closest: tuple[float, float] | None = None
    for line in road.lines:
        for left, right in zip(line, line[1:], strict=False):
            # SLA is stored longitude/latitude; route geometry is latitude/longitude.
            distance = _point_segment_distance(midpoint, (left[1], left[0]), (right[1], right[0]))
            if closest is None or distance < closest[0]:
                closest = distance, _bearing((left[1], left[0]), (right[1], right[0]))
    if closest is None or closest[0] > config.major_road_sustained_entry_buffer_m:
        return False
    # SLA line orientation is not a traffic-direction guarantee, hence either
    # alignment direction is valid; the topology catalogue carries direction.
    difference = abs((route_heading - closest[1] + 180) % 360 - 180)
    difference = min(difference, 180 - difference)
    return difference <= config.major_road_max_alignment_difference_degrees


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
