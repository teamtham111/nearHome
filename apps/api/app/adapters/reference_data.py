"""Load LTA/MRT/MOE reference snapshots from fixtures or live API."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "data_pipeline" / "fixtures"


@dataclass
class BusStop:
    stop_code: str
    description: str
    latitude: float
    longitude: float
    road_name: str
    services: list[str]


@dataclass
class MrtStation:
    station_code: str
    name: str
    latitude: float
    longitude: float
    lines: list[str]
    is_interchange: bool


@dataclass
class School:
    school_name: str
    level: str
    latitude: float
    longitude: float
    address: str


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_minutes(distance_m: float, speed_m_per_min: float = 80.0) -> float:
    """Approximate walk time — OneMap routing replaces this when live."""
    return round(distance_m / speed_m_per_min, 1)


class ReferenceDataStore:
    """Singleton-style loader for transport and school reference snapshots."""

    _bus_stops: list[BusStop] | None = None
    _mrt_stations: list[MrtStation] | None = None
    _schools: list[School] | None = None

    @classmethod
    def bus_stops(cls) -> list[BusStop]:
        if cls._bus_stops is None:
            path = FIXTURES / "lta_bus_stops.json"
            raw = json.loads(path.read_text()) if path.exists() else []
            cls._bus_stops = [
                BusStop(
                    stop_code=r["stop_code"],
                    description=r["description"],
                    latitude=r["latitude"],
                    longitude=r["longitude"],
                    road_name=r["road_name"],
                    services=r.get("services", []),
                )
                for r in raw
            ]
        return cls._bus_stops

    @classmethod
    def mrt_stations(cls) -> list[MrtStation]:
        if cls._mrt_stations is None:
            path = FIXTURES / "mrt_stations.json"
            raw = json.loads(path.read_text()) if path.exists() else []
            seen: set[str] = set()
            stations: list[MrtStation] = []
            for r in raw:
                key = r["name"].upper()
                if key in seen:
                    continue
                seen.add(key)
                stations.append(
                    MrtStation(
                        station_code=r["station_code"],
                        name=r["name"],
                        latitude=r["latitude"],
                        longitude=r["longitude"],
                        lines=r.get("lines", []),
                        is_interchange=r.get("is_interchange", False),
                    )
                )
            cls._mrt_stations = stations
        return cls._mrt_stations

    @classmethod
    def schools(cls) -> list[School]:
        if cls._schools is None:
            path = FIXTURES / "moe_schools.json"
            raw = json.loads(path.read_text()) if path.exists() else []
            cls._schools = [School(**r) for r in raw]
        return cls._schools

    @classmethod
    def nearest_mrt(cls, lat: float, lng: float) -> tuple[MrtStation | None, float | None, float | None]:
        best: MrtStation | None = None
        best_dist = float("inf")
        for st in cls.mrt_stations():
            d = haversine_m(lat, lng, st.latitude, st.longitude)
            if d < best_dist:
                best_dist = d
                best = st
        if best is None:
            return None, None, None
        return best, best_dist, walk_minutes(best_dist)

    @classmethod
    def nearby_bus_stops(cls, lat: float, lng: float, max_walk_min: float = 8.0) -> list[tuple[BusStop, float]]:
        results: list[tuple[BusStop, float]] = []
        for stop in cls.bus_stops():
            dist = haversine_m(lat, lng, stop.latitude, stop.longitude)
            mins = walk_minutes(dist)
            if mins <= max_walk_min:
                results.append((stop, mins))
        return sorted(results, key=lambda x: x[1])

    @classmethod
    def nearby_schools(cls, lat: float, lng: float, max_km: float = 2.0) -> list[tuple[School, float]]:
        results: list[tuple[School, float]] = []
        for school in cls.schools():
            dist_m = haversine_m(lat, lng, school.latitude, school.longitude)
            if dist_m <= max_km * 1000:
                results.append((school, dist_m / 1000))
        return sorted(results, key=lambda x: x[1])
