"""Opt-in, offline Google Roads corroboration for SLA→OSM Major Road matches.

Never imported by the API/worker.  ``--allow-google`` is required before an
HTTP request is possible; without it the command is a deterministic dry run.
Google Roads is corroborating evidence, not ground truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pyproj import Transformer
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
import sys

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

SCHEMA_VERSION = "google-roads-corroboration-v1"
SNAP_URL = "https://roads.googleapis.com/v1/snapToRoads"
DEFAULT_OUTPUT = ROOT / "data_pipeline" / "validation" / "major_road_google_roads"
DEFAULT_CACHE = ROOT / "data_pipeline" / "cache" / "google_roads_validation"
DEFAULT_LABELS = ROOT / "data_pipeline" / "fixtures" / "major_road_google_roads_gold_labels.json"
CONTROLLED_REPRODUCTION_ROADS = ("JALAN BAHAR", "ADMIRALTY ROAD WEST")


@dataclass(frozen=True)
class AuditConfig:
    sample_spacing_metres: float = 100.0  # < Google's recommended 300 m maximum gap.
    max_points_per_request: int = 100
    geometry_agreement_metres: float = 35.0  # review threshold, not matcher threshold.
    high_segment_overlap: float = 0.80
    medium_segment_overlap: float = 0.45
    high_geometry_overlap: float = 0.80
    medium_geometry_overlap: float = 0.55
    minimum_snapped_points: int = 2
    cache_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class RequestContext:
    """Non-secret identity of one validator request."""

    road: str
    source: str
    chunk_number: int
    chunk_count: int
    label: str | None = None


class GoogleRoadsRequestError(RuntimeError):
    """A safe error whose detail has already been recorded in request_trace."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample_lines(lines: list[list[tuple[float, float]]] | tuple[tuple[tuple[float, float], ...], ...], spacing_m: float) -> list[tuple[float, float]]:
    """Regular SVY21-distance samples returned as WGS84 (lat, lon)."""
    forward = Transformer.from_crs("EPSG:4326", "EPSG:3414", always_xy=True)
    reverse = Transformer.from_crs("EPSG:3414", "EPSG:4326", always_xy=True)
    result: list[tuple[float, float]] = []
    for raw in lines:
        if len(raw) < 2:
            continue
        line = LineString([forward.transform(lon, lat) for lon, lat in raw])
        count = max(2, math.ceil(line.length / spacing_m) + 1)
        for number in range(count):
            point = line.interpolate(number / (count - 1), normalized=True)
            lon, lat = reverse.transform(point.x, point.y)
            if not result or result[-1] != (lat, lon):
                result.append((lat, lon))
    return result


def _chunks(points: list[tuple[float, float]], size: int) -> list[list[tuple[float, float]]]:
    # Retain one boundary point so adjacent Snap-to-Roads chunks remain continuous.
    return [points[start : start + size] for start in range(0, len(points), size - 1)] if points else []


class GoogleRoadsCache:
    def __init__(self, directory: Path, version: str) -> None:
        self.directory, self.version = directory, version

    def key(self, points: list[tuple[float, float]]) -> str:
        payload = {"operation": "snapToRoads", "interpolate": True, "points": points, "version": self.version}
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

    def get(self, points: list[tuple[float, float]]) -> dict[str, Any] | None:
        path = self.directory / f"{self.key(points)}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def put(self, points: list[tuple[float, float]], payload: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{self.key(points)}.json").write_text(json.dumps(payload, indent=2, sort_keys=True))


class GoogleRoadsClient:
    def __init__(self, key: str, cache: GoogleRoadsCache, *, allow_google: bool, max_requests: int) -> None:
        self.key, self.cache, self.allow_google, self.max_requests = key, cache, allow_google, max_requests
        self.requests_made = 0
        self.request_trace: list[dict[str, Any]] = []

    def _safe_error(self, response: httpx.Response) -> dict[str, Any]:
        """Keep Google's explanatory fields, never a secret-bearing request URL."""
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = body.get("error", {}) if isinstance(body, dict) else {}
        if not isinstance(error, dict):
            error = {}
        # Google errors are expected to be public diagnostic text, but redact the
        # configured key defensively before writing an artifact.
        def redact(value: Any) -> Any:
            if isinstance(value, str):
                return value.replace(self.key, "[REDACTED]") if self.key else value
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, dict):
                return {str(key): redact(item) for key, item in value.items()}
            return value

        return redact(
            {
                "google_error": error,
                "google_error_code": error.get("code"),
                "google_error_status": error.get("status"),
                "google_error_message": error.get("message"),
                "google_error_details": error.get("details"),
            }
        )

    def _request_metadata(self, points: list[tuple[float, float]], context: RequestContext) -> dict[str, Any]:
        if not points:
            raise ValueError("Google Roads requires at least one point.")
        if len(points) > AuditConfig.max_points_per_request:
            raise ValueError(f"Google Roads point limit exceeded: {len(points)} > {AuditConfig.max_points_per_request}.")
        if any(not math.isfinite(latitude) or not math.isfinite(longitude) for latitude, longitude in points):
            raise ValueError("Google Roads path contains a non-finite coordinate.")
        path = "|".join(f"{latitude:.7f},{longitude:.7f}" for latitude, longitude in points)
        # Preserve the exact key length when measuring the encoded query, without
        # constructing a URL that contains the actual key in an artifact.
        safe_params = {"path": path, "interpolate": "true", "key": "x" * len(self.key)}
        encoded_query_length = len(str(httpx.Request("GET", SNAP_URL, params=safe_params).url).split("?", 1)[-1])
        return {
            "road": context.road,
            "source": context.source,
            "chunk": f"{context.chunk_number}/{context.chunk_count}",
            "label": context.label,
            "point_count": len(points),
            "path_character_count": len(path),
            "encoded_query_character_count": encoded_query_length,
            "endpoint": SNAP_URL,
            "method": "GET",
            "api_version": "v1",
            "interpolate": True,
            "timeout_seconds": 20,
            "authentication": "query_api_key_present",
        }

    def snap(
        self,
        points: list[tuple[float, float]],
        config: AuditConfig,
        context: RequestContext,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        metadata = self._request_metadata(points, context)
        cached = self.cache.get(points) if use_cache else None
        if cached is not None:
            self.request_trace.append({**metadata, "transport": "cache", "http_status": 200})
            return cached
        if not self.allow_google:
            raise RuntimeError("Dry run: use --allow-google and GOOGLE_ROADS_API_KEY to make Google Roads calls.")
        if not self.key:
            raise RuntimeError("GOOGLE_ROADS_API_KEY is required with --allow-google.")
        if self.requests_made >= self.max_requests:
            raise RuntimeError(f"Google Roads request cap ({self.max_requests}) reached.")
        params = {"path": "|".join(f"{lat:.7f},{lon:.7f}" for lat, lon in points), "interpolate": "true", "key": self.key}
        for attempt in range(3):
            started_at = datetime.now(UTC).isoformat()
            response = httpx.get(SNAP_URL, params=params, timeout=20)
            self.requests_made += 1
            trace = {
                **metadata,
                "transport": "http",
                "request_sequence": self.requests_made,
                "attempt": attempt + 1,
                "timestamp": started_at,
                "http_status": response.status_code,
            }
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                if response.is_error:
                    trace.update(self._safe_error(response))
                    self.request_trace.append(trace)
                    raise GoogleRoadsRequestError(
                        f"Google Roads HTTP {response.status_code}: "
                        f"{trace.get('google_error_status', 'UNKNOWN')}"
                    )
                payload = response.json()
                self.cache.put(points, payload)
                self.request_trace.append(trace)
                return payload
            self.request_trace.append(trace)
            time.sleep(2**attempt)
        raise AssertionError("unreachable")


def _snapped_points(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [point for payload in payloads for point in payload.get("snappedPoints", []) if "location" in point]


def _snap_geometry(
    client: GoogleRoadsClient,
    *,
    road: str,
    source: str,
    samples: list[tuple[float, float]],
    config: AuditConfig,
    label: str | None = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    chunks = _chunks(samples, config.max_points_per_request)
    return _snapped_points(
        [
            client.snap(
                chunk,
                config,
                RequestContext(road, source, index, len(chunks), label),
                use_cache=use_cache,
            )
            for index, chunk in enumerate(chunks, 1)
        ]
    )


def _compressed_ids(points: list[dict[str, Any]]) -> list[str]:
    ids = [str(point["placeId"]) for point in points if point.get("placeId")]
    return [value for index, value in enumerate(ids) if not index or value != ids[index - 1]]


def _lcs(left: list[str], right: list[str]) -> int:
    prior = [0] * (len(right) + 1)
    for item in left:
        current = [0]
        for index, other in enumerate(right, 1):
            current.append(prior[index - 1] + 1 if item == other else max(prior[index], current[-1]))
        prior = current
    return prior[-1]


def compare_google_evidence(sla: list[dict[str, Any]], osm: list[dict[str, Any]], config: AuditConfig) -> dict[str, Any]:
    """Pure comparator: suitable for synthetic/adversarial tests without Google."""
    sla_ids, osm_ids = _compressed_ids(sla), _compressed_ids(osm)
    sla_set, osm_set = set(sla_ids), set(osm_ids)
    segment_overlap = len(sla_set & osm_set) / len(sla_set | osm_set) if sla_set or osm_set else None
    ordered = _lcs(sla_ids, osm_ids) / min(len(sla_ids), len(osm_ids)) if sla_ids and osm_ids else None
    transform = Transformer.from_crs("EPSG:4326", "EPSG:3414", always_xy=True)
    def line(points: list[dict[str, Any]]) -> LineString | None:
        coords = [transform.transform(p["location"]["longitude"], p["location"]["latitude"]) for p in points]
        return LineString(coords) if len(coords) >= 2 else None
    sla_line, osm_line = line(sla), line(osm)
    symmetric_overlap = median_distance = None
    if sla_line and osm_line:
        buffer = config.geometry_agreement_metres
        symmetric_overlap = min(
            sla_line.intersection(osm_line.buffer(buffer)).length / max(sla_line.length, 1),
            osm_line.intersection(sla_line.buffer(buffer)).length / max(osm_line.length, 1),
        )
        distances = [osm_line.distance(sla_line.interpolate(i / 20, normalized=True)) for i in range(21)]
        median_distance = sorted(distances)[len(distances) // 2]
    valid = len(sla) >= config.minimum_snapped_points and len(osm) >= config.minimum_snapped_points
    if not valid:
        classification, reasons = "UNVALIDATABLE", ["INSUFFICIENT_GOOGLE_SNAPS"]
    elif (
        segment_overlap is not None
        and segment_overlap >= config.high_segment_overlap
        and ((symmetric_overlap or 0) >= config.high_geometry_overlap or (median_distance or float("inf")) <= config.geometry_agreement_metres)
    ):
        classification, reasons = "HIGH_CONFIDENCE", ["STRONG_GOOGLE_SEGMENT_AND_GEOMETRY_AGREEMENT"]
    elif (segment_overlap or 0) >= config.medium_segment_overlap or (symmetric_overlap or 0) >= config.medium_geometry_overlap:
        classification, reasons = "MEDIUM_CONFIDENCE", ["COMPATIBLE_GOOGLE_CORRIDOR_EVIDENCE"]
    elif (symmetric_overlap or 0) < 0.2 and (segment_overlap or 0) < 0.15:
        classification, reasons = "LIKELY_INCORRECT", ["GOOGLE_CORRIDORS_DISAGREE"]
    else:
        classification, reasons = "REVIEW", ["AMBIGUOUS_OR_PARTIAL_GOOGLE_AGREEMENT"]
    return {"classification": classification, "reason_codes": reasons, "segment_overlap": segment_overlap, "ordered_segment_agreement": ordered, "symmetric_geometry_overlap": symmetric_overlap, "median_snapped_geometry_distance_metres": median_distance, "sla_snap_success_rate": None, "osm_snap_success_rate": None}


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Google Roads corroboration for SLA→OSM mapping")
    parser.add_argument("--allow-google", action="store_true", help="Explicitly permit billable Google Roads requests")
    parser.add_argument("--max-requests", type=int, default=20)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--road", action="append", default=[])
    parser.add_argument(
        "--reproduce-403",
        action="store_true",
        help="Run four uncached 8-point SLA probes: Jalan Bahar, Admiralty Road West, then repeat both.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()
    from app.adapters.transport_data.major_road_network import (
        LocalDriveGraph,
        SlaMajorRoadStore,
        SlaOsmMajorRoadMappingStore,
    )
    roads = SlaMajorRoadStore.load()
    graph = LocalDriveGraph.from_graphml(ROOT / "data_pipeline/fixtures/singapore-drive.graphml")
    mapping = SlaOsmMajorRoadMappingStore.load()
    if mapping is None:
        raise RuntimeError(
            "The versioned SLA→OSM mapping artifact is missing or incompatible; rebuild it before Google corroboration."
        )
    selected = [road for road in roads if not args.road or road.identifier in args.road][: args.limit]
    config = AuditConfig(); planned = 0
    for road in selected:
        predicted = mapping.road_for(road.identifier)
        edge_ids = set(predicted.matched_edge_ids) if predicted else set()
        osm_lines = [edge.coordinates for edge in graph.edges if edge.identifier in edge_ids]
        planned += 2 * math.ceil(max(len(sample_lines(road.lines, config.sample_spacing_metres)), len(sample_lines(osm_lines, config.sample_spacing_metres))) / config.max_points_per_request)
    if args.reproduce_403:
        selected = [road for road in roads if road.identifier in CONTROLLED_REPRODUCTION_ROADS]
        if len(selected) != len(CONTROLLED_REPRODUCTION_ROADS):
            raise RuntimeError("The controlled reproduction roads are missing from the SLA fixture.")
        planned = 4
    if not args.allow_google:
        print(json.dumps({"dry_run": True, "roads": len(selected), "estimated_upper_bound_requests": planned, "message": "No HTTP calls made. Add --allow-google to run."}, indent=2)); return 0
    client = GoogleRoadsClient(os.environ.get("GOOGLE_ROADS_API_KEY", ""), GoogleRoadsCache(args.cache_dir, config.cache_version), allow_google=True, max_requests=args.max_requests)
    rows=[]
    if args.reproduce_403:
        by_identifier = {road.identifier: road for road in selected}
        reproduction_rows = []
        for run_number, road_identifier in enumerate(("JALAN BAHAR", "ADMIRALTY ROAD WEST") * 2, 1):
            road = by_identifier[road_identifier]
            samples = sample_lines(road.lines, config.sample_spacing_metres)[:8]
            try:
                _snap_geometry(
                    client,
                    road=road.identifier,
                    source="SLA",
                    samples=samples,
                    config=config,
                    label=f"controlled_reproduction_{run_number}",
                    use_cache=False,
                )
                reproduction_rows.append({"run": run_number, "road": road.identifier, "status": "SUCCESS"})
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                reproduction_rows.append({"run": run_number, "road": road.identifier, "status": "ERROR", "error": str(exc)})
        rows = [{"controlled_reproduction": reproduction_rows}]
    for road in selected:
        if args.reproduce_403:
            break
        predicted = mapping.road_for(road.identifier); edge_ids=set(predicted.matched_edge_ids) if predicted else set(); osm_lines=[edge.coordinates for edge in graph.edges if edge.identifier in edge_ids]
        sla_samples, osm_samples = sample_lines(road.lines, config.sample_spacing_metres), sample_lines(osm_lines, config.sample_spacing_metres)
        try:
            sla = _snap_geometry(client, road=road.identifier, source="SLA", samples=sla_samples, config=config)
            osm = _snap_geometry(client, road=road.identifier, source="OSM", samples=osm_samples, config=config)
            row = compare_google_evidence(sla, osm, config)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            row = {"classification":"UNVALIDATABLE","reason_codes":["GOOGLE_REQUEST_ERROR"],"error":str(exc)}
        rows.append({"sla_feature_id":road.identifier,"sla_name":road.name,"matched_osm_edge_ids":[list(x) for x in sorted(edge_ids)],"sla_sample_count":len(sla_samples),"osm_sample_count":len(osm_samples),**row})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    provenance={"timestamp":datetime.now(UTC).isoformat(),"schema":SCHEMA_VERSION,"git_commit":subprocess.getoutput("git rev-parse HEAD"),"config":asdict(config),"google_requests_made":client.requests_made}
    (args.output_dir/"google_roads_report.json").write_text(json.dumps({"provenance":provenance,"rows":rows,"request_trace":client.request_trace},indent=2))
    classifications = Counter(r["classification"] for r in rows if "classification" in r)
    print(json.dumps({"roads":len(rows),"requests_made":client.requests_made,"classifications":dict(classifications)},indent=2)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
