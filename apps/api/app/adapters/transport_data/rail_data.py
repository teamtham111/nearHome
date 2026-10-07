"""Loads the curated rail graph fixtures (see `data_pipeline/build_rail_graph.py`)
into typed dataclasses for `app.networks.rail_graph`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from app.adapters.provider_schemas.reference import RailEdgeSnapshotRow, RailStationSnapshot

ROOT = Path(__file__).resolve().parents[5]
RAIL_DIR = ROOT / "data_pipeline" / "fixtures" / "rail"
_RAIL_EDGE_ROWS_ADAPTER = TypeAdapter(list[RailEdgeSnapshotRow])


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
            payload = RailStationSnapshot.model_validate_json(stations_path.read_text())
            source = payload.source
            version = payload.version
            for station_row in payload.stations:
                stations.append(
                    RailStation(
                        station_name=station_row.station_name,
                        codes=tuple(station_row.codes),
                        lines=tuple(station_row.lines),
                        is_interchange=station_row.is_interchange,
                        latitude=station_row.latitude,
                        longitude=station_row.longitude,
                        active=station_row.active,
                    )
                )

        edges: list[RailEdge] = []
        if edges_path.exists():
            with edges_path.open() as fh:
                rows = _RAIL_EDGE_ROWS_ADAPTER.validate_python(list(csv.DictReader(fh)))
                for edge_row in rows:
                    edges.append(
                        RailEdge(
                            from_node=edge_row.from_node,
                            to_node=edge_row.to_node,
                            line=edge_row.line,
                            edge_type=edge_row.edge_type,
                            estimated_minutes=edge_row.estimated_minutes,
                        )
                    )

        cls._data = RailGraphData(stations=stations, edges=edges, source=source, version=version)
        return cls._data

    @classmethod
    def reset_cache(cls) -> None:
        cls._data = None
