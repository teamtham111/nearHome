"""Loads the curated rail graph fixtures (see `data_pipeline/build_rail_graph.py`)
into typed dataclasses for `app.networks.rail_graph`.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
RAIL_DIR = ROOT / "data_pipeline" / "fixtures" / "rail"


@dataclass(frozen=True)
class RailStation:
    station_name: str
    codes: tuple[str, ...]
    lines: tuple[str, ...]
    is_interchange: bool
    latitude: float | None
    longitude: float | None
    active: bool


@dataclass(frozen=True)
class RailEdge:
    from_node: str
    to_node: str
    line: str
    edge_type: str  # "ride" | "transfer"
    estimated_minutes: float


@dataclass
class RailGraphData:
    stations: list[RailStation]
    edges: list[RailEdge]
    source: str
    version: str

    def code_to_station_name(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for station in self.stations:
            for code in station.codes:
                mapping[code] = station.station_name
        return mapping

    def station_by_name(self, name: str) -> RailStation | None:
        for station in self.stations:
            if station.station_name.lower() == name.lower():
                return station
        return None


class RailDataStore:
    _data: RailGraphData | None = None

    @classmethod
    def load(cls) -> RailGraphData:
        if cls._data is not None:
            return cls._data

        stations_path = RAIL_DIR / "rail_stations.json"
        edges_path = RAIL_DIR / "rail_edges.csv"

        stations: list[RailStation] = []
        source = "no rail data loaded"
        version = "unknown"
        if stations_path.exists():
            payload = json.loads(stations_path.read_text())
            source = payload.get("source", source)
            version = payload.get("version", version)
            for row in payload.get("stations", []):
                stations.append(
                    RailStation(
                        station_name=row["station_name"],
                        codes=tuple(row["codes"]),
                        lines=tuple(row["lines"]),
                        is_interchange=row["is_interchange"],
                        latitude=row.get("latitude"),
                        longitude=row.get("longitude"),
                        active=row.get("active", True),
                    )
                )

        edges: list[RailEdge] = []
        if edges_path.exists():
            with edges_path.open() as fh:
                for row in csv.DictReader(fh):
                    edges.append(
                        RailEdge(
                            from_node=row["from_node"],
                            to_node=row["to_node"],
                            line=row["line"],
                            edge_type=row["edge_type"],
                            estimated_minutes=float(row["estimated_minutes"]),
                        )
                    )

        cls._data = RailGraphData(stations=stations, edges=edges, source=source, version=version)
        return cls._data

    @classmethod
    def reset_cache(cls) -> None:
        cls._data = None
