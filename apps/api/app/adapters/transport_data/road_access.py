"""Loads the curated road-access-points fixture (see
`data_pipeline/build_road_access_points.py`) for the Driving model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.adapters.reference_data import haversine_m

ROOT = Path(__file__).resolve().parents[5]
FIXTURE = ROOT / "data_pipeline" / "fixtures" / "road_access_points.json"


@dataclass(frozen=True)
class RoadAccessPoint:
    name: str
    expressway: str
    direction_label: str
    latitude: float
    longitude: float


class RoadAccessPointStore:
    _points: list[RoadAccessPoint] | None = None
    _source: str = ""
    _version: str = ""

    @classmethod
    def load(cls) -> list[RoadAccessPoint]:
        if cls._points is not None:
            return cls._points
        if not FIXTURE.exists():
            cls._points = []
            return cls._points
        payload = json.loads(FIXTURE.read_text())
        cls._source = payload.get("source", "")
        cls._version = payload.get("version", "")
        cls._points = [
            RoadAccessPoint(
                name=p["name"],
                expressway=p["expressway"],
                direction_label=p["direction_label"],
                latitude=p["latitude"],
                longitude=p["longitude"],
            )
            for p in payload.get("access_points", [])
        ]
        return cls._points

    @classmethod
    def is_usable(cls) -> bool:
        return len(cls.load()) >= 10

    @classmethod
    def nearby(cls, latitude: float, longitude: float, max_distance_m: float, limit: int = 6) -> list[RoadAccessPoint]:
        """Haversine pre-filter ONLY — candidate shortlist for the engine to
        then rank by routed driving duration."""
        scored = [
            (haversine_m(latitude, longitude, p.latitude, p.longitude), p)
            for p in cls.load()
        ]
        scored.sort(key=lambda t: t[0])
        candidates = [p for dist, p in scored if dist <= max_distance_m]
        if not candidates and scored:
            # Fall back to the nearest few regardless of radius rather than
            # returning nothing — the engine still validates via routed
            # duration before accepting any of them.
            candidates = [p for _dist, p in scored[:limit]]
        return candidates[:limit]

    @classmethod
    def reset_cache(cls) -> None:
        cls._points = None
