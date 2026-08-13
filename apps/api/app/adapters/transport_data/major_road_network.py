"""Offline SLA Major_Road geometry plus OSM drive-network access.

SLA's National Map Line layer says *which* roads are Major Roads.  A persisted
OSMnx ``network_type=drive`` graph says whether and how a vehicle can actually
enter them.  This module intentionally never downloads either dataset at
request time.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from numbers import Integral
from pathlib import Path
from typing import Any

from app.core.config import settings

ROOT = Path(__file__).resolve().parents[5]
DEFAULT_SLA_FIXTURE = ROOT / "data_pipeline" / "fixtures" / "sla_major_roads.geojson"
DEFAULT_GRAPH_FIXTURE = ROOT / "data_pipeline" / "fixtures" / "singapore-drive.graphml"
DEFAULT_MAPPING_FIXTURE = ROOT / "data_pipeline" / "fixtures" / "sla_osm_major_road_mapping.json"
_METRES_PER_DEGREE_LATITUDE = 111_320.0
MAPPING_SCHEMA_VERSION = 2
MAPPING_ALGORITHM_VERSION = "sla-osm-strtree-entry-catalogue-v2"
CATALOGUE_VERSION = "major-road-access-catalogue-v2"


@dataclass(frozen=True)
class SlaMajorRoad:
    identifier: str
    name: str
    lines: tuple[tuple[tuple[float, float], ...], ...]  # longitude, latitude


@dataclass(frozen=True)
class DriveNode:
    identifier: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class DriveEdge:
    source: str
    target: str
    key: str
    coordinates: tuple[tuple[float, float], ...]  # longitude, latitude
    name: str | None
    length_metres: float

    @property
    def identifier(self) -> tuple[str, str, str]:
        return self.source, self.target, self.key


@dataclass(frozen=True)
class MajorRoadEntryPoint:
    """A directed OSM junction from which a driver enters an SLA Major Road."""

    name: str
    major_road_id: str
    node_id: str
    latitude: float
    longitude: float
    matched_edge_ids: tuple[tuple[str, str, str], ...]
    # The junction is evidence of a legal entry.  The online routing target is
    # deliberately a short distance along its directed major-road edge so the
    # returned route can prove sustained entry rather than merely terminate at
    # a nearby junction or frontage road.
    candidate_id: str = ""
    target_latitude: float | None = None
    target_longitude: float | None = None
    approach_edge_ids: tuple[tuple[str, str, str], ...] = ()
    matching_confidence: str = "TOPOLOGY_VALIDATED"

    @property
    def routing_coordinate(self) -> tuple[float, float]:
        return (
            self.target_latitude if self.target_latitude is not None else self.latitude,
            self.target_longitude if self.target_longitude is not None else self.longitude,
        )


@dataclass(frozen=True)
class PrecomputedMajorRoad:
    """Offline SLA-to-OSM match and entry topology for one official road."""

    identifier: str
    name: str
    matched_edge_ids: tuple[tuple[str, str, str], ...]
    entry_nodes: tuple[MajorRoadEntryPoint, ...]
    name_supported_edge_count: int


@dataclass(frozen=True)
class MajorRoadMapping:
    """Versioned static mapping tied to one exact OSM GraphML artifact."""

    graph_sha256: str
    sla_sha256: str
    generated_at: str
    algorithm_version: str
    search_radius_metres: float
    roads_by_identifier: dict[str, PrecomputedMajorRoad]
    catalogue_version: str = CATALOGUE_VERSION

    def road_for(self, identifier: str) -> PrecomputedMajorRoad | None:
        return self.roads_by_identifier.get(identifier)


@dataclass(frozen=True)
class MajorRoadMappingBuildReport:
    total_roads: int
    mapped_roads: int
    unmapped_roads: int
    roads_without_entries: int
    matched_edges: int
    entry_nodes: int
    geometry_only_edges: int
    suspicious_roads: tuple[str, ...]


@dataclass(frozen=True)
class SlaOsmEdgeMatchEvaluation:
    """One precise-match decision, reusable by builds and validation tooling."""

    edge: DriveEdge
    distance_metres: float
    name_supported: bool
    required_tolerance_metres: float
    aligned: bool
    accepted: bool
    rejection_reason: str | None


@dataclass(frozen=True)
class OSMEdgeSTRtree:
    """Projected OSM edge index used only by offline build/validation tools."""

    tree: Any
    geometries: tuple[Any, ...]
    edges: tuple[DriveEdge, ...]
    transformer: Any


def _metres_xy(longitude: float, latitude: float, reference_latitude: float) -> tuple[float, float]:
    return (
        longitude * _METRES_PER_DEGREE_LATITUDE * math.cos(math.radians(reference_latitude)),
        latitude * _METRES_PER_DEGREE_LATITUDE,
    )


def _point_segment_distance_metres(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    reference_latitude = (point[1] + start[1] + end[1]) / 3
    px, py = _metres_xy(*point, reference_latitude)
    ax, ay = _metres_xy(*start, reference_latitude)
    bx, by = _metres_xy(*end, reference_latitude)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    fraction = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + fraction * dx), py - (ay + fraction * dy))


def _polyline_distance_metres(point: tuple[float, float], line: tuple[tuple[float, float], ...]) -> float:
    if len(line) < 2:
        return float("inf")
    return min(_point_segment_distance_metres(point, start, end) for start, end in zip(line, line[1:], strict=False))


def _lines_distance_metres(first: tuple[tuple[float, float], ...], second: tuple[tuple[float, float], ...]) -> float:
    # Endpoint-to-segment comparisons capture the small source offsets expected
    # between SLA and OSM while avoiding a heavyweight geometry dependency at
    # request time.
    return min(
        *(_polyline_distance_metres(point, second) for point in first),
        *(_polyline_distance_metres(point, first) for point in second),
    )


def _road_bounds(road: SlaMajorRoad) -> tuple[float, float, float, float]:
    points = [point for line in road.lines for point in line]
    longitudes, latitudes = zip(*points, strict=True)
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


def _edge_can_be_within_tolerance(
    edge: DriveEdge, road_bounds: tuple[float, float, float, float], tolerance_metres: float
) -> bool:
    """Cheap, conservative WGS84 bounding-box rejection before exact matching."""
    min_lon, min_lat, max_lon, max_lat = road_bounds
    # Use the southern/northern extremes with a small safety margin so this
    # preliminary check cannot reject any edge that the existing local metric
    # could regard as within tolerance anywhere in Singapore.
    latitude_padding = (tolerance_metres + 1.0) / _METRES_PER_DEGREE_LATITUDE
    minimum_cosine = min(math.cos(math.radians(min_lat)), math.cos(math.radians(max_lat)))
    longitude_padding = (tolerance_metres + 1.0) / (_METRES_PER_DEGREE_LATITUDE * minimum_cosine)
    edge_lons = [point[0] for point in edge.coordinates]
    edge_lats = [point[1] for point in edge.coordinates]
    return not (
        max(edge_lons) < min_lon - longitude_padding
        or min(edge_lons) > max_lon + longitude_padding
        or max(edge_lats) < min_lat - latitude_padding
        or min(edge_lats) > max_lat + latitude_padding
    )


def _normalise_road_name(value: str | None) -> set[str]:
    if not value:
        return set()
    aliases = {"RD": "ROAD", "AVE": "AVENUE", "ST": "STREET"}
    generic = {"ROAD", "AVENUE", "STREET", "DRIVE", "LANE", "WAY", "JALAN", "LORONG"}
    tokens = (
        aliases.get(part, part) for part in "".join(char if char.isalnum() else " " for char in value.upper()).split()
    )
    return {token for token in tokens if token and token not in generic}


def _names_support_match(sla_name: str, osm_name: str | None) -> bool:
    left, right = _normalise_road_name(sla_name), _normalise_road_name(osm_name)
    return bool(left and right and left & right)


def _has_line_alignment(
    sla_lines: tuple[tuple[tuple[float, float], ...], ...], edge_line: tuple[tuple[float, float], ...], tolerance: float
) -> bool:
    """Avoid treating an approach edge that merely touches/crosses a road as it."""
    close_points = sum(
        min(_polyline_distance_metres(point, line) for line in sla_lines) <= tolerance for point in edge_line
    )
    return close_points >= 2


def _edge_matches_sla_road(
    road: SlaMajorRoad,
    edge: DriveEdge,
    match_tolerance_metres: float,
    spatial_only_tolerance_metres: float,
) -> bool:
    """Apply the canonical strict SLA/OSM rules to one candidate edge."""
    return evaluate_sla_osm_edge_match(road, edge, match_tolerance_metres, spatial_only_tolerance_metres).accepted


def evaluate_sla_osm_edge_match(
    road: SlaMajorRoad,
    edge: DriveEdge,
    match_tolerance_metres: float,
    spatial_only_tolerance_metres: float,
    *,
    road_bounds: tuple[float, float, float, float] | None = None,
) -> SlaOsmEdgeMatchEvaluation:
    """Apply the canonical matcher and expose its deterministic evidence.

    The distance/alignment implementation intentionally remains the existing
    local metres-per-degree calculation.  STRtree is candidate discovery only;
    exhaustive and indexed validation both call this exact function.
    """
    bounds = _road_bounds(road) if road_bounds is None else road_bounds
    name_supported = _names_support_match(road.name, edge.name)
    required_tolerance = match_tolerance_metres if name_supported else spatial_only_tolerance_metres
    if not _edge_can_be_within_tolerance(edge, bounds, match_tolerance_metres):
        return SlaOsmEdgeMatchEvaluation(
            edge, float("inf"), name_supported, required_tolerance, False, False, "outside_broad_bounding_box"
        )
    distance = min(_lines_distance_metres(line, edge.coordinates) for line in road.lines)
    if distance > match_tolerance_metres:
        return SlaOsmEdgeMatchEvaluation(
            edge, distance, name_supported, required_tolerance, False, False, "outside_broad_tolerance"
        )
    if distance > required_tolerance:
        return SlaOsmEdgeMatchEvaluation(
            edge, distance, name_supported, required_tolerance, False, False, "outside_required_tolerance"
        )
    aligned = _has_line_alignment(road.lines, edge.coordinates, required_tolerance)
    return SlaOsmEdgeMatchEvaluation(
        edge,
        distance,
        name_supported,
        required_tolerance,
        aligned,
        aligned,
        None if aligned else "insufficient_line_alignment",
    )


class LocalDriveGraph:
    """Small immutable projection of a persisted OSMnx directed graph."""

    _SPATIAL_INDEX_CELL_DEGREES = 0.002

    def __init__(self, nodes: list[DriveNode], edges: list[DriveEdge]) -> None:
        self.nodes = {node.identifier: node for node in nodes}
        self.edges = tuple(edges)
        self._incoming: dict[str, list[DriveEdge]] = {}
        self._outgoing: dict[str, list[DriveEdge]] = {}
        self._edge_grid: dict[tuple[int, int], list[DriveEdge]] = {}
        self._follow_cache: dict[tuple[tuple[str, str, str], tuple[str, str, str], int], bool] = {}
        for edge in edges:
            if edge.source not in self.nodes or edge.target not in self.nodes:
                continue
            self._incoming.setdefault(edge.target, []).append(edge)
            self._outgoing.setdefault(edge.source, []).append(edge)
            self._add_to_spatial_index(edge)

    @classmethod
    def from_graphml(cls, path: Path) -> LocalDriveGraph:
        try:
            import osmnx as ox
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("OSMnx is required to load the persisted Singapore drive graph.") from exc
        graph = ox.load_graphml(path)
        nodes = [
            DriveNode(str(node_id), float(data["y"]), float(data["x"]))
            for node_id, data in graph.nodes(data=True)
            if data.get("x") is not None and data.get("y") is not None
        ]
        edges: list[DriveEdge] = []
        for source, target, key, data in graph.edges(keys=True, data=True):
            source_node, target_node = str(source), str(target)
            geometry = data.get("geometry")
            if geometry is not None and hasattr(geometry, "coords"):
                coordinates = tuple((float(lon), float(lat)) for lon, lat, *_ in geometry.coords)
            else:
                source_data, target_data = graph.nodes[source], graph.nodes[target]
                coordinates = (
                    (float(source_data["x"]), float(source_data["y"])),
                    (float(target_data["x"]), float(target_data["y"])),
                )
            name = data.get("name")
            if isinstance(name, list):
                name = " / ".join(str(item) for item in name)
            edges.append(
                DriveEdge(
                    source_node,
                    target_node,
                    str(key),
                    coordinates,
                    str(name) if name else None,
                    float(data.get("length", 0.0)),
                )
            )
        return cls(nodes, edges)

    def incoming(self, node_id: str) -> tuple[DriveEdge, ...]:
        return tuple(self._incoming.get(node_id, []))

    def nearby_edges(self, latitude: float, longitude: float, radius_metres: float) -> tuple[DriveEdge, ...]:
        """Return locally indexed edge candidates for route-polyline matching."""
        latitude_delta = radius_metres / _METRES_PER_DEGREE_LATITUDE
        longitude_delta = radius_metres / max(1.0, _METRES_PER_DEGREE_LATITUDE * math.cos(math.radians(latitude)))
        cells = self._grid_cells_for_bounds(
            longitude - longitude_delta,
            latitude - latitude_delta,
            longitude + longitude_delta,
            latitude + latitude_delta,
        )
        seen: set[tuple[str, str, str]] = set()
        return tuple(
            edge
            for cell in cells
            for edge in self._edge_grid.get(cell, [])
            if not (edge.identifier in seen or seen.add(edge.identifier))
        )

    def can_follow(self, previous: DriveEdge, current: DriveEdge, max_hops: int = 6) -> bool:
        """Whether a short directed OSM connection preserves route progression."""
        if previous.identifier == current.identifier or previous.target == current.source:
            return True
        cache_key = (previous.identifier, current.identifier, max_hops)
        if cache_key in self._follow_cache:
            return self._follow_cache[cache_key]
        frontier = [(previous.target, 0)]
        seen = {previous.target}
        while frontier:
            node_id, hops = frontier.pop(0)
            if hops >= max_hops:
                continue
            for edge in self._outgoing.get(node_id, []):
                if edge.target == current.source:
                    self._follow_cache[cache_key] = True
                    return True
                if edge.target not in seen:
                    seen.add(edge.target)
                    frontier.append((edge.target, hops + 1))
        self._follow_cache[cache_key] = False
        return False

    def _add_to_spatial_index(self, edge: DriveEdge) -> None:
        longitudes = [point[0] for point in edge.coordinates]
        latitudes = [point[1] for point in edge.coordinates]
        for cell in self._grid_cells_for_bounds(min(longitudes), min(latitudes), max(longitudes), max(latitudes)):
            self._edge_grid.setdefault(cell, []).append(edge)

    def _grid_cells_for_bounds(
        self, min_longitude: float, min_latitude: float, max_longitude: float, max_latitude: float
    ) -> tuple[tuple[int, int], ...]:
        size = self._SPATIAL_INDEX_CELL_DEGREES
        return tuple(
            (longitude_cell, latitude_cell)
            for longitude_cell in range(math.floor(min_longitude / size), math.floor(max_longitude / size) + 1)
            for latitude_cell in range(math.floor(min_latitude / size), math.floor(max_latitude / size) + 1)
        )


def find_candidate_sla_major_roads(
    roads: tuple[SlaMajorRoad, ...], latitude: float, longitude: float, safety_radius_metres: float, limit: int
) -> list[SlaMajorRoad]:
    """Return nearest distinct SLA Major Roads; distance is discovery only.

    SLA source rows can contain multiple segments for one road. The persisted
    store already groups them, but retaining this identity guard makes callers
    safe when they provide ungrouped rows (including tests/import tools).
    """
    point = (longitude, latitude)
    nearby = sorted(
        [(min(_polyline_distance_metres(point, line) for line in road.lines), road) for road in roads if road.lines],
        key=lambda item: (item[0], item[1].name.upper(), item[1].identifier),
    )
    selected: list[SlaMajorRoad] = []
    seen: set[str] = set()
    for distance, road in nearby:
        if distance > safety_radius_metres:
            break
        identity = road.name.strip().upper() or road.identifier.strip().upper()
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(road)
        if len(selected) >= limit:
            break
    return selected


def match_sla_road_to_osm_edges(
    road: SlaMajorRoad,
    graph: LocalDriveGraph,
    match_tolerance_metres: float,
    spatial_only_tolerance_metres: float,
    *,
    candidate_edges: Iterable[DriveEdge] | None = None,
) -> list[DriveEdge]:
    """Match an SLA road against supplied OSM candidates using strict rules.

    Production enrichment does not call this function.  The offline mapping
    builder supplies STRtree-local candidates; the default full graph remains
    only for small synthetic tests and one-off data validation tooling.
    """
    edges = graph.edges if candidate_edges is None else candidate_edges
    road_bounds = _road_bounds(road)
    return [
        edge
        for edge in edges
        if evaluate_sla_osm_edge_match(
            road,
            edge,
            match_tolerance_metres,
            spatial_only_tolerance_metres,
            road_bounds=road_bounds,
        ).accepted
    ]


def find_major_road_entry_nodes(
    road: SlaMajorRoad,
    matched_edges: list[DriveEdge],
    graph: LocalDriveGraph,
    dedup_metres: float,
    limit: int,
    target_distance_metres: float = 80.0,
) -> list[MajorRoadEntryPoint]:
    """Return nodes with a non-major incoming edge and a matched major outgoing edge."""
    matched_ids = {edge.identifier for edge in matched_edges}
    by_node: dict[str, list[DriveEdge]] = {}
    for edge in matched_edges:
        by_node.setdefault(edge.source, []).append(edge)
    entries: list[MajorRoadEntryPoint] = []
    for node_id, outgoing_major_edges in by_node.items():
        approach_edges = tuple(
            sorted(
                (edge for edge in graph.incoming(node_id) if edge.identifier not in matched_ids),
                key=lambda edge: edge.identifier,
            )
        )
        if not approach_edges:
            continue
        node = graph.nodes[node_id]
        if any(
            _point_segment_distance_metres(
                (node.longitude, node.latitude),
                (existing.longitude, existing.latitude),
                (existing.longitude, existing.latitude),
            )
            <= dedup_metres
            for existing in entries
        ):
            continue
        # The outgoing matched edge is directed from this junction into the
        # Major Road.  Its downstream point is the online routing hypothesis.
        primary_major_edge = min(outgoing_major_edges, key=lambda edge: edge.identifier)
        target_longitude, target_latitude = _coordinate_along_edge(primary_major_edge, target_distance_metres)
        candidate_id = f"{road.identifier}:{node_id}:{primary_major_edge.target}:{primary_major_edge.key}"
        entries.append(
            MajorRoadEntryPoint(
                name=road.name,
                major_road_id=road.identifier,
                node_id=node_id,
                latitude=node.latitude,
                longitude=node.longitude,
                matched_edge_ids=tuple(sorted(edge.identifier for edge in outgoing_major_edges)),
                candidate_id=candidate_id,
                target_latitude=target_latitude,
                target_longitude=target_longitude,
                approach_edge_ids=tuple(edge.identifier for edge in approach_edges),
            )
        )
        if len(entries) >= limit:
            break
    return entries


def _coordinate_along_edge(edge: DriveEdge, target_distance_metres: float) -> tuple[float, float]:
    """Return a directed point along an OSM edge, clamped before its end."""
    coordinates = edge.coordinates
    if len(coordinates) < 2:
        return coordinates[0] if coordinates else (0.0, 0.0)
    remaining = max(0.0, target_distance_metres)
    for start, end in zip(coordinates, coordinates[1:], strict=False):
        length = _point_segment_distance_metres(end, start, start)
        if length <= 0:
            continue
        if remaining <= length:
            fraction = remaining / length
            return start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction
        remaining -= length
    return coordinates[-1]


class SlaMajorRoadStore:
    @staticmethod
    @lru_cache(maxsize=1)
    def load() -> tuple[SlaMajorRoad, ...]:
        path = Path(settings.sla_major_roads_path) if settings.sla_major_roads_path else DEFAULT_SLA_FIXTURE
        if not path.exists():
            return ()
        payload = json.loads(path.read_text())
        grouped: dict[str, tuple[str, list[tuple[tuple[float, float], ...]]]] = {}
        for feature in payload.get("features", []):
            properties = feature.get("properties", {})
            if properties.get("FOLDERPATH") != "Layers/Major_Road":
                continue
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates", [])
            lines = coordinates if geometry.get("type") == "MultiLineString" else [coordinates]
            name = str(properties.get("NAME") or "Unnamed SLA Major Road")
            # National Map Line stores a road as many segments. Grouping by
            # official name lets matching assess its local alignment instead
            # of incorrectly treating one segment endpoint as the whole road.
            key = name.upper()
            for line in lines:
                cleaned = tuple((float(point[0]), float(point[1])) for point in line if len(point) >= 2)
                if len(cleaned) >= 2:
                    grouped.setdefault(key, (name, []))[1].append(cleaned)
        return tuple(SlaMajorRoad(key, name, tuple(lines)) for key, (name, lines) in grouped.items())

    @classmethod
    def reset_cache(cls) -> None:
        cls.load.cache_clear()


class SingaporeDriveGraphStore:
    @staticmethod
    @lru_cache(maxsize=1)
    def load() -> LocalDriveGraph | None:
        path = (
            Path(settings.singapore_drive_graph_path) if settings.singapore_drive_graph_path else DEFAULT_GRAPH_FIXTURE
        )
        if not path.exists():
            return None
        try:
            return LocalDriveGraph.from_graphml(path)
        except Exception:  # noqa: BLE001 - corrupt/incompatible artifact is unavailable evidence
            return None

    @classmethod
    def reset_cache(cls) -> None:
        cls.load.cache_clear()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping_path() -> Path:
    if settings.sla_osm_major_road_mapping_path:
        return Path(settings.sla_osm_major_road_mapping_path)
    return DEFAULT_MAPPING_FIXTURE


def _graph_path() -> Path:
    return Path(settings.singapore_drive_graph_path) if settings.singapore_drive_graph_path else DEFAULT_GRAPH_FIXTURE


def _sla_path() -> Path:
    return Path(settings.sla_major_roads_path) if settings.sla_major_roads_path else DEFAULT_SLA_FIXTURE


def _projected_geometries(graph: LocalDriveGraph) -> tuple[list[Any], list[DriveEdge]]:
    """Return SVY21 OSM edge LineStrings in the same order as their edges."""
    try:
        from pyproj import Transformer
        from shapely.geometry import LineString
    except ImportError as exc:  # pragma: no cover - installation invariant
        raise RuntimeError("pyproj and shapely are required to build the Major Road mapping.") from exc
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3414", always_xy=True)
    geometries: list[Any] = []
    edges: list[DriveEdge] = []
    for edge in graph.edges:
        if len(edge.coordinates) < 2:
            continue
        geometries.append(LineString([transformer.transform(lon, lat) for lon, lat in edge.coordinates]))
        edges.append(edge)
    return geometries, edges


def build_osm_edge_strtree(graph: LocalDriveGraph) -> OSMEdgeSTRtree:
    """Build one deterministic EPSG:3414 STRtree with positional edge identity."""
    try:
        from pyproj import Transformer
        from shapely.strtree import STRtree
    except ImportError as exc:  # pragma: no cover - installation invariant
        raise RuntimeError("pyproj and shapely are required to build the Major Road mapping.") from exc
    geometries, edges = _projected_geometries(graph)
    return OSMEdgeSTRtree(
        tree=STRtree(geometries),
        geometries=tuple(geometries),
        edges=tuple(edges),
        transformer=Transformer.from_crs("EPSG:4326", "EPSG:3414", always_xy=True),
    )


def strtree_candidate_edges(
    road: SlaMajorRoad,
    index: OSMEdgeSTRtree,
    search_radius_metres: float,
) -> tuple[list[DriveEdge], Any]:
    """Return broad local candidates; strict matching still happens afterwards."""
    try:
        from shapely.geometry import MultiLineString
    except ImportError as exc:  # pragma: no cover - installation invariant
        raise RuntimeError("shapely is required to build the Major Road mapping.") from exc
    projected = MultiLineString(
        [[index.transformer.transform(lon, lat) for lon, lat in line] for line in road.lines if len(line) >= 2]
    )
    if projected.is_empty:
        return [], projected
    results = index.tree.query(projected.buffer(search_radius_metres))
    # Shapely 2 returns integer positions, preserving duplicate/equal geometry
    # identities. The compatibility path deliberately maps identical WKBs to
    # *all* positions rather than dropping equal geometries.
    if len(results) == 0:
        return [], projected
    if isinstance(results[0], Integral):
        # GEOS does not promise a meaningful query-result order.  Sort by the
        # stable directed OSM edge identifier so downstream entry-node capping
        # cannot depend on STRtree internals.
        return sorted((index.edges[int(position)] for position in results), key=lambda edge: edge.identifier), projected
    positions_by_wkb: dict[bytes, list[int]] = {}
    for position, geometry in enumerate(index.geometries):
        positions_by_wkb.setdefault(geometry.wkb, []).append(position)
    positions = [position for geometry in results for position in positions_by_wkb.get(geometry.wkb, [])]
    return sorted((index.edges[position] for position in positions), key=lambda edge: edge.identifier), projected


def build_major_road_mapping(
    roads: tuple[SlaMajorRoad, ...],
    graph: LocalDriveGraph,
    *,
    match_tolerance_metres: float,
    spatial_only_tolerance_metres: float,
    entry_node_dedup_metres: float,
    max_entry_nodes_per_road: int,
    entry_target_distance_metres: float = 80.0,
    search_radius_metres: float = 100.0,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[MajorRoadMapping, MajorRoadMappingBuildReport]:
    """Build all SLA→OSM matches once using an STRtree candidate index."""
    strtree_index = build_osm_edge_strtree(graph)
    geometry_by_edge_id = {
        edge.identifier: geometry for edge, geometry in zip(strtree_index.edges, strtree_index.geometries, strict=True)
    }
    mapped: dict[str, PrecomputedMajorRoad] = {}
    suspicious: list[str] = []
    matched_edge_count = entry_node_count = geometry_only_edge_count = roads_without_entries = 0

    for road_number, road in enumerate(roads, start=1):
        candidate_edges, projected_road = strtree_candidate_edges(road, strtree_index, search_radius_metres)
        # GEOS cheaply removes broad STRtree hits that cannot possibly meet
        # the existing 35 m maximum. The canonical name/tolerance/alignment
        # logic below still makes the actual match decision.
        strict_candidates = [
            edge
            for edge in candidate_edges
            if geometry_by_edge_id[edge.identifier].distance(projected_road) <= match_tolerance_metres
        ]
        matched_edges = match_sla_road_to_osm_edges(
            road,
            graph,
            match_tolerance_metres,
            spatial_only_tolerance_metres,
            candidate_edges=strict_candidates,
        )
        if not matched_edges:
            suspicious.append(f"{road.name}: no matched OSM edges")
            continue
        entries = find_major_road_entry_nodes(
            road, matched_edges, graph, entry_node_dedup_metres, max_entry_nodes_per_road, entry_target_distance_metres
        )
        name_supported = sum(_names_support_match(road.name, edge.name) for edge in matched_edges)
        geometry_only = len(matched_edges) - name_supported
        if not entries:
            roads_without_entries += 1
            suspicious.append(f"{road.name}: no valid OSM entry nodes")
        if len(matched_edges) > 500:
            suspicious.append(f"{road.name}: unusually many matched OSM edges ({len(matched_edges)})")
        if geometry_only:
            suspicious.append(f"{road.name}: {geometry_only} geometry-only edge match(es)")
        mapped[road.identifier] = PrecomputedMajorRoad(
            identifier=road.identifier,
            name=road.name,
            matched_edge_ids=tuple(sorted(edge.identifier for edge in matched_edges)),
            entry_nodes=tuple(entries),
            name_supported_edge_count=name_supported,
        )
        matched_edge_count += len(matched_edges)
        entry_node_count += len(entries)
        geometry_only_edge_count += geometry_only
        if progress_callback is not None:
            progress_callback(road_number, len(roads), road.name)

    mapping = MajorRoadMapping(
        graph_sha256="",
        sla_sha256="",
        generated_at=datetime.now(UTC).isoformat(),
        algorithm_version=MAPPING_ALGORITHM_VERSION,
        search_radius_metres=search_radius_metres,
        roads_by_identifier=mapped,
    )
    return mapping, MajorRoadMappingBuildReport(
        total_roads=len(roads),
        mapped_roads=len(mapped),
        unmapped_roads=len(roads) - len(mapped),
        roads_without_entries=roads_without_entries,
        matched_edges=matched_edge_count,
        entry_nodes=entry_node_count,
        geometry_only_edges=geometry_only_edge_count,
        suspicious_roads=tuple(suspicious),
    )


def write_major_road_mapping(
    path: Path,
    mapping: MajorRoadMapping,
    *,
    graph_sha256: str,
    sla_sha256: str,
) -> None:
    """Persist a portable, versioned mapping artifact."""
    payload = {
        "schema_version": MAPPING_SCHEMA_VERSION,
        "metadata": {
            "graph_sha256": graph_sha256,
            "sla_sha256": sla_sha256,
            "generated_at": mapping.generated_at,
            "matching_algorithm_version": mapping.algorithm_version,
            "strtree_search_radius_metres": mapping.search_radius_metres,
            "catalogue_version": mapping.catalogue_version,
        },
        "major_roads": {
            identifier: {
                "name": road.name,
                "matched_osm_edge_ids": [list(edge_id) for edge_id in road.matched_edge_ids],
                "name_supported_edge_count": road.name_supported_edge_count,
                "entry_nodes": [
                    {
                        "node_id": point.node_id,
                        "latitude": point.latitude,
                        "longitude": point.longitude,
                        "matched_osm_edge_ids": [list(edge_id) for edge_id in point.matched_edge_ids],
                        "candidate_id": point.candidate_id,
                        "target_latitude": point.target_latitude,
                        "target_longitude": point.target_longitude,
                        "approach_osm_edge_ids": [list(edge_id) for edge_id in point.approach_edge_ids],
                        "matching_confidence": point.matching_confidence,
                    }
                    for point in road.entry_nodes
                ],
            }
            for identifier, road in sorted(mapping.roads_by_identifier.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


class SlaOsmMajorRoadMappingStore:
    """Load only a mapping proven to belong to the installed GraphML/SLA files."""

    _last_error: str | None = None

    @staticmethod
    @lru_cache(maxsize=1)
    def load() -> MajorRoadMapping | None:
        mapping_path, graph_path, sla_path = _mapping_path(), _graph_path(), _sla_path()
        if not mapping_path.exists():
            SlaOsmMajorRoadMappingStore._last_error = "SLA-to-OSM Major Road mapping artifact is missing."
            return None
        if not graph_path.exists() or not sla_path.exists():
            SlaOsmMajorRoadMappingStore._last_error = "Major Road mapping source artifact is missing."
            return None
        try:
            payload = json.loads(mapping_path.read_text())
            if payload.get("schema_version") != MAPPING_SCHEMA_VERSION:
                raise ValueError("unsupported mapping schema version")
            metadata = payload["metadata"]
            graph_sha256 = str(metadata["graph_sha256"])
            sla_sha256 = str(metadata["sla_sha256"])
            if graph_sha256 != _sha256_file(graph_path):
                raise ValueError("mapping graph hash does not match singapore-drive.graphml")
            if sla_sha256 != _sha256_file(sla_path):
                raise ValueError("mapping SLA hash does not match sla_major_roads.geojson")
            if metadata.get("matching_algorithm_version") != MAPPING_ALGORITHM_VERSION:
                raise ValueError("unsupported mapping algorithm version")
            if metadata.get("catalogue_version") != CATALOGUE_VERSION:
                raise ValueError("unsupported major-road access catalogue version")
            roads: dict[str, PrecomputedMajorRoad] = {}
            for identifier, raw in payload["major_roads"].items():
                if not isinstance(raw, dict) or not isinstance(raw.get("entry_nodes"), list):
                    raise ValueError(f"malformed road mapping for {identifier}")
                edge_ids = tuple(tuple(str(part) for part in edge) for edge in raw.get("matched_osm_edge_ids", []))
                entries = tuple(
                    MajorRoadEntryPoint(
                        name=str(raw["name"]),
                        major_road_id=str(identifier),
                        node_id=str(entry["node_id"]),
                        latitude=float(entry["latitude"]),
                        longitude=float(entry["longitude"]),
                        matched_edge_ids=tuple(
                            tuple(str(part) for part in edge) for edge in entry.get("matched_osm_edge_ids", [])
                        ),
                        candidate_id=str(entry["candidate_id"]),
                        target_latitude=float(entry["target_latitude"]),
                        target_longitude=float(entry["target_longitude"]),
                        approach_edge_ids=tuple(
                            tuple(str(part) for part in edge) for edge in entry.get("approach_osm_edge_ids", [])
                        ),
                        matching_confidence=str(entry.get("matching_confidence", "TOPOLOGY_VALIDATED")),
                    )
                    for entry in raw["entry_nodes"]
                )
                roads[str(identifier)] = PrecomputedMajorRoad(
                    identifier=str(identifier),
                    name=str(raw["name"]),
                    matched_edge_ids=edge_ids,
                    entry_nodes=entries,
                    name_supported_edge_count=int(raw.get("name_supported_edge_count", 0)),
                )
            SlaOsmMajorRoadMappingStore._last_error = None
            return MajorRoadMapping(
                graph_sha256=graph_sha256,
                sla_sha256=sla_sha256,
                generated_at=str(metadata["generated_at"]),
                algorithm_version=str(metadata["matching_algorithm_version"]),
                search_radius_metres=float(metadata["strtree_search_radius_metres"]),
                roads_by_identifier=roads,
                catalogue_version=str(metadata["catalogue_version"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            SlaOsmMajorRoadMappingStore._last_error = f"Invalid SLA-to-OSM Major Road mapping artifact: {exc}"
            return None

    @classmethod
    def validation_error(cls) -> str | None:
        cls.load()
        return cls._last_error

    @classmethod
    def reset_cache(cls) -> None:
        cls.load.cache_clear()
        cls._last_error = None


def validate_major_road_mapping_artifacts() -> None:
    """Fail fast at live-process startup rather than halfway through a job."""
    if SlaOsmMajorRoadMappingStore.load() is None:
        raise RuntimeError(SlaOsmMajorRoadMappingStore.validation_error() or "Major Road mapping is unavailable.")
