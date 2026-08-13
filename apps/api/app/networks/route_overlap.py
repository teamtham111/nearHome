"""Deterministic physical-overlap classification for driving alternatives.

Google's high-quality route polyline is map-matched locally onto directed OSM
edges. Shared matched *distance*, not instruction road names, is the primary
independence signal. Geometry and the previous road-name heuristic are explicit
lower-confidence fallbacks when an OSM match is unavailable or unreliable.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from app.adapters.routing.base import RouteResult
from app.adapters.transport_data.major_road_network import DriveEdge, LocalDriveGraph

OverlapClass = Literal["independent", "partially_independent", "substantially_overlapping", "not_practical"]
OverlapMethod = Literal["osm_edge_match", "hybrid", "polyline_geometry", "road_name_fallback"]
MatchConfidence = Literal["HIGH", "MEDIUM", "LOW", "UNAVAILABLE"]

INDEPENDENT_MAX_OVERLAP = 0.30
PARTIALLY_INDEPENDENT_MAX_OVERLAP = 0.70
NOT_PRACTICAL_PENALTY_MINUTES = 15.0

# Matching evaluates direction, geometry, and graph progression together.
OSM_MATCH_RADIUS_METRES = 35.0
OSM_MATCH_MAX_HEADING_DIFFERENCE_DEGREES = 70.0
OSM_MATCH_MAX_CANDIDATES_PER_SEGMENT = 6
OSM_MATCH_AMBIGUITY_COST_METRES = 6.0
OSM_MATCH_UNMATCHED_COST_METRES = 100.0
OSM_MATCH_DISCONNECTED_TRANSITION_COST_METRES = 110.0
OSM_MATCH_CONNECTED_GAP_COST_METRES = 12.0
GEOMETRIC_FALLBACK_TOLERANCE_METRES = 25.0
_METRES_PER_DEGREE_LATITUDE = 111_320.0

_ROAD_SUFFIXES = (
    "Road|Ave|Avenue|Expressway|Highway|St|Street|Rd|Dr|Drive|Way|Lane|Ln|Blvd|Boulevard|Link"
    "|PIE|CTE|AYE|ECP|BKE|KJE|KPE|MCE|SLE|TPE"
)
_ROAD_TOKEN_PATTERN = re.compile(
    r"\b([A-Z][a-zA-Z']*(?:\s+[A-Z][a-zA-Z']*){0,3}(?:\s+(?:" + _ROAD_SUFFIXES + r"))\b)"
)


@dataclass(frozen=True)
class MapMatchResult:
    """Distance-attributed directed OSM match for one route polyline."""

    edge_distances_metres: dict[tuple[str, str, str], float]
    matched_distance_metres: float
    total_route_distance_metres: float
    matched_fraction: float
    ambiguous_fraction: float
    unmatched_fraction: float
    discontinuity_fraction: float
    confidence: MatchConfidence
    edge_names: dict[tuple[str, str, str], str | None]


@dataclass(frozen=True)
class OverlapResult:
    classification: OverlapClass
    overlap_ratio: float
    duration_penalty_minutes: float
    shared_roads: list[str]
    primary_roads: list[str]
    alternative_roads: list[str]
    evidence_note: str
    overlap_method: OverlapMethod
    primary_match: MapMatchResult | None = None
    alternative_match: MapMatchResult | None = None
    geometric_overlap_ratio: float | None = None


def decode_google_polyline(encoded: str | None) -> list[tuple[float, float]]:
    """Decode Google's encoded polyline to ordered ``(latitude, longitude)`` points."""
    if not encoded:
        return []
    coordinates: list[tuple[float, float]] = []
    latitude = longitude = index = 0
    try:
        while index < len(encoded):
            latitude_delta, index = _decode_polyline_value(encoded, index)
            longitude_delta, index = _decode_polyline_value(encoded, index)
            latitude += latitude_delta
            longitude += longitude_delta
            coordinates.append((latitude / 100_000, longitude / 100_000))
    except (IndexError, ValueError):
        return []
    return coordinates


def _decode_polyline_value(encoded: str, index: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if index >= len(encoded):
            raise ValueError("truncated encoded polyline")
        value = ord(encoded[index]) - 63
        index += 1
        result |= (value & 0x1F) << shift
        shift += 5
        if value < 0x20:
            break
    return (-(result >> 1) if result & 1 else result >> 1), index


def map_match_route(route: RouteResult, graph: LocalDriveGraph | None) -> MapMatchResult | None:
    """Map-match a route progressively; never independently snap each point.

    Dynamic programming chooses the lowest-cost sequence of directed edge
    candidates. A transition is cheap only if the graph preserves forward
    connectivity, so close frontage roads, opposite carriageways, and crossing
    streets cannot replace a connected route merely because they are nearest.
    """
    coordinates = decode_google_polyline(route.encoded_polyline)
    if graph is None or len(coordinates) < 2:
        return None
    segments = [
        (start, end, _segment_length_metres(start, end))
        for start, end in zip(coordinates, coordinates[1:], strict=False)
    ]
    segments = [segment for segment in segments if segment[2] > 0.5]
    total_distance = sum(length for _start, _end, length in segments)
    if not segments or total_distance <= 0:
        return None

    candidate_lists = [_edge_candidates(start, end, graph) for start, end, _length in segments]
    states: list[dict[tuple[str, str, str] | None, tuple[float, list[DriveEdge | None]]]] = []
    first_candidates = candidate_lists[0]
    states.append(
        {
            edge.identifier if edge is not None else None: (cost, [edge])
            for edge, cost in [*first_candidates, (None, OSM_MATCH_UNMATCHED_COST_METRES)]
        }
    )
    for candidates in candidate_lists[1:]:
        options = [*candidates, (None, OSM_MATCH_UNMATCHED_COST_METRES)]
        prior = states[-1]
        current: dict[tuple[str, str, str] | None, tuple[float, list[DriveEdge | None]]] = {}
        for edge, base_cost in options:
            edge_id = edge.identifier if edge is not None else None
            best: tuple[float, list[DriveEdge | None]] | None = None
            for _prior_id, (prior_cost, prior_path) in prior.items():
                previous = prior_path[-1]
                candidate_cost = prior_cost + base_cost + _transition_cost(previous, edge, graph)
                if best is None or candidate_cost < best[0]:
                    best = (candidate_cost, [*prior_path, edge])
            if best is not None and (edge_id not in current or best[0] < current[edge_id][0]):
                current[edge_id] = best
        states.append(current)
    path = min(states[-1].values(), key=lambda item: item[0])[1]

    attributed: defaultdict[tuple[str, str, str], float] = defaultdict(float)
    names: dict[tuple[str, str, str], str | None] = {}
    matched_distance = ambiguous_distance = unmatched_distance = discontinuity_distance = 0.0
    for index, ((_start, _end, length), selected, candidates) in enumerate(
        zip(segments, path, candidate_lists, strict=True)
    ):
        if selected is None:
            unmatched_distance += length
            continue
        attributed[selected.identifier] += length
        names[selected.identifier] = selected.name
        matched_distance += length
        selected_cost = next(cost for edge, cost in candidates if edge.identifier == selected.identifier)
        if sum(cost - selected_cost <= OSM_MATCH_AMBIGUITY_COST_METRES for _edge, cost in candidates) > 1:
            ambiguous_distance += length
        if index and path[index - 1] is not None and not graph.can_follow(path[index - 1], selected):
            discontinuity_distance += length

    # A route may cross one OSM edge over several Google segments. Never claim
    # more shared road distance than that directed edge physically contains.
    edge_lengths = {edge.identifier: edge.length_metres for candidates in candidate_lists for edge, _cost in candidates}
    capped = {edge_id: min(distance, edge_lengths.get(edge_id, distance)) for edge_id, distance in attributed.items()}
    effective_matched_distance = sum(capped.values())
    matched_fraction = effective_matched_distance / total_distance
    confidence = _match_confidence(
        matched_fraction,
        ambiguous_distance / total_distance,
        unmatched_distance / total_distance,
        discontinuity_distance / total_distance,
    )
    return MapMatchResult(
        edge_distances_metres={edge_id: round(distance, 1) for edge_id, distance in capped.items()},
        matched_distance_metres=round(effective_matched_distance, 1),
        total_route_distance_metres=round(total_distance, 1),
        matched_fraction=round(matched_fraction, 3),
        ambiguous_fraction=round(ambiguous_distance / total_distance, 3),
        unmatched_fraction=round(unmatched_distance / total_distance, 3),
        discontinuity_fraction=round(discontinuity_distance / total_distance, 3),
        confidence=confidence,
        edge_names=names,
    )


def _edge_candidates(
    start: tuple[float, float], end: tuple[float, float], graph: LocalDriveGraph
) -> list[tuple[DriveEdge, float]]:
    midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    heading = _bearing_degrees(start, end)
    candidates: list[tuple[DriveEdge, float]] = []
    for edge in graph.nearby_edges(midpoint[0], midpoint[1], OSM_MATCH_RADIUS_METRES):
        distance = _point_polyline_distance_metres(midpoint, [(lat, lon) for lon, lat in edge.coordinates])
        if distance > OSM_MATCH_RADIUS_METRES:
            continue
        difference = _heading_difference_degrees(heading, _edge_heading_degrees(edge))
        if difference > OSM_MATCH_MAX_HEADING_DIFFERENCE_DEGREES:
            continue
        candidates.append((edge, distance + difference / 180 * OSM_MATCH_RADIUS_METRES))
    return sorted(candidates, key=lambda item: (item[1], item[0].identifier))[:OSM_MATCH_MAX_CANDIDATES_PER_SEGMENT]


def _transition_cost(previous: DriveEdge | None, current: DriveEdge | None, graph: LocalDriveGraph) -> float:
    if previous is None or current is None:
        return 0.0
    if previous.identifier == current.identifier or previous.target == current.source:
        return 0.0
    return (
        OSM_MATCH_CONNECTED_GAP_COST_METRES
        if graph.can_follow(previous, current)
        else OSM_MATCH_DISCONNECTED_TRANSITION_COST_METRES
    )


def _match_confidence(
    matched_fraction: float, ambiguous_fraction: float, unmatched_fraction: float, discontinuity_fraction: float
) -> MatchConfidence:
    if matched_fraction >= 0.85 and ambiguous_fraction <= 0.15 and discontinuity_fraction <= 0.10:
        return "HIGH"
    if matched_fraction >= 0.60 and unmatched_fraction <= 0.40 and discontinuity_fraction <= 0.30:
        return "MEDIUM"
    return "LOW"


def _edge_overlap(primary: MapMatchResult, alternative: MapMatchResult) -> float:
    shared = sum(
        min(primary.edge_distances_metres[edge_id], alternative.edge_distances_metres[edge_id])
        for edge_id in primary.edge_distances_metres.keys() & alternative.edge_distances_metres.keys()
    )
    primary_ratio = shared / primary.matched_distance_metres if primary.matched_distance_metres else 0.0
    alternative_ratio = shared / alternative.matched_distance_metres if alternative.matched_distance_metres else 0.0
    return (primary_ratio + alternative_ratio) / 2


def symmetric_geometric_overlap(primary: RouteResult, alternative: RouteResult) -> float | None:
    """Length-weighted symmetric 25 m polyline buffer approximation."""
    first = decode_google_polyline(primary.encoded_polyline)
    second = decode_google_polyline(alternative.encoded_polyline)
    if len(first) < 2 or len(second) < 2:
        return None
    return round((_polyline_fraction_within(first, second) + _polyline_fraction_within(second, first)) / 2, 3)


def _polyline_fraction_within(points: list[tuple[float, float]], target: list[tuple[float, float]]) -> float:
    total = contained = 0.0
    for start, end in zip(points, points[1:], strict=False):
        length = _segment_length_metres(start, end)
        if length <= 0:
            continue
        total += length
        midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        if _point_polyline_distance_metres(midpoint, target) <= GEOMETRIC_FALLBACK_TOLERANCE_METRES:
            contained += length
    return contained / total if total else 0.0


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def extract_road_tokens(route: RouteResult) -> set[str]:
    """Final legacy fallback when no usable polyline overlap is available."""
    tokens: set[str] = set()
    for step in route.route_steps:
        for match in _ROAD_TOKEN_PATTERN.finditer(_strip_html(step.instruction)):
            tokens.add(match.group(1).strip().upper())
    return tokens


def classify_alternative(
    primary: RouteResult,
    alternative: RouteResult,
    independent_max: float = INDEPENDENT_MAX_OVERLAP,
    partially_independent_max: float = PARTIALLY_INDEPENDENT_MAX_OVERLAP,
    not_practical_penalty_minutes: float = NOT_PRACTICAL_PENALTY_MINUTES,
    *,
    graph: LocalDriveGraph | None = None,
    primary_match: MapMatchResult | None = None,
) -> OverlapResult:
    """Classify with OSM overlap first and disclose every fallback level."""
    primary_match = primary_match or map_match_route(primary, graph)
    alternative_match = map_match_route(alternative, graph)
    geometry_overlap = symmetric_geometric_overlap(primary, alternative)
    primary_roads, alternative_roads = extract_road_tokens(primary), extract_road_tokens(alternative)
    shared_roads = primary_roads & alternative_roads

    if primary_match and alternative_match and primary_match.confidence == alternative_match.confidence == "HIGH":
        overlap = _edge_overlap(primary_match, alternative_match)
        method: OverlapMethod = "osm_edge_match"
        note = "High-confidence directed OSM map matches; overlap is symmetric shared matched-edge distance."
    elif primary_match and alternative_match and (
        primary_match.confidence in {"HIGH", "MEDIUM"} and alternative_match.confidence in {"HIGH", "MEDIUM"}
    ) and geometry_overlap is not None:
        overlap = 0.75 * _edge_overlap(primary_match, alternative_match) + 0.25 * geometry_overlap
        method = "hybrid"
        note = "Medium-confidence OSM map matches combined with symmetric 25 m geometric overlap."
    elif geometry_overlap is not None:
        overlap = geometry_overlap
        method = "polyline_geometry"
        note = "Low-confidence/unavailable OSM match; overlap is symmetric 25 m geometric fallback only."
    else:
        if primary_roads and alternative_roads:
            overlap = len(shared_roads) / len(primary_roads | alternative_roads)
            note = "No usable route polyline; legacy road-name Jaccard fallback from turn-by-turn instructions."
        else:
            shorter = min(primary.distance_metres, alternative.distance_metres)
            longer = max(primary.distance_metres, alternative.distance_metres)
            overlap = shorter / longer if longer else 0.0
            note = "No usable polyline or road names; weak legacy distance-similarity fallback, not confirmed overlap."
        method = "road_name_fallback"

    duration_penalty = round(alternative.duration_minutes - primary.duration_minutes, 1)
    if duration_penalty > not_practical_penalty_minutes:
        classification: OverlapClass = "not_practical"
    elif overlap <= independent_max:
        classification = "independent"
    elif overlap <= partially_independent_max:
        classification = "partially_independent"
    else:
        classification = "substantially_overlapping"
    return OverlapResult(
        classification=classification,
        overlap_ratio=round(overlap, 2),
        duration_penalty_minutes=duration_penalty,
        shared_roads=sorted(shared_roads),
        primary_roads=sorted(primary_roads),
        alternative_roads=sorted(alternative_roads),
        evidence_note=note,
        overlap_method=method,
        primary_match=primary_match,
        alternative_match=alternative_match,
        geometric_overlap_ratio=geometry_overlap,
    )


def _metres_xy(latitude: float, longitude: float, reference_latitude: float) -> tuple[float, float]:
    return (
        longitude * _METRES_PER_DEGREE_LATITUDE * math.cos(math.radians(reference_latitude)),
        latitude * _METRES_PER_DEGREE_LATITUDE,
    )


def _point_polyline_distance_metres(point: tuple[float, float], line: list[tuple[float, float]]) -> float:
    return min(
        _point_segment_distance_metres(point, start, end) for start, end in zip(line, line[1:], strict=False)
    )


def _point_segment_distance_metres(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    reference = (point[0] + start[0] + end[0]) / 3
    px, py = _metres_xy(*point, reference)
    ax, ay = _metres_xy(*start, reference)
    bx, by = _metres_xy(*end, reference)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    fraction = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + fraction * dx), py - (ay + fraction * dy))


def _segment_length_metres(start: tuple[float, float], end: tuple[float, float]) -> float:
    reference = (start[0] + end[0]) / 2
    start_x, start_y = _metres_xy(*start, reference)
    end_x, end_y = _metres_xy(*end, reference)
    return math.hypot(end_x - start_x, end_y - start_y)


def _bearing_degrees(start: tuple[float, float], end: tuple[float, float]) -> float:
    latitude1, longitude1, latitude2, longitude2 = map(math.radians, (*start, *end))
    y = math.sin(longitude2 - longitude1) * math.cos(latitude2)
    x = (
        math.cos(latitude1) * math.sin(latitude2)
        - math.sin(latitude1) * math.cos(latitude2) * math.cos(longitude2 - longitude1)
    )
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _edge_heading_degrees(edge: DriveEdge) -> float:
    start, end = edge.coordinates[0], edge.coordinates[-1]
    return _bearing_degrees((start[1], start[0]), (end[1], end[0]))


def _heading_difference_degrees(first: float, second: float) -> float:
    return abs((first - second + 180) % 360 - 180)
