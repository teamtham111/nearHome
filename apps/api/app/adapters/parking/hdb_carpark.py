"""Official HDB carpark static-data provider and nearby matcher."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.adapters.reference_data import haversine_m

ROOT = Path(__file__).resolve().parents[5]
FIXTURE = ROOT / "data_pipeline" / "fixtures" / "hdb_carparks.json"
SOURCE_NAME = "data.gov.sg HDB Carpark Information"


def _optional_int(value: Any) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalize_carpark_type(value: str | None) -> str | None:
    """Keep a small stable vocabulary while retaining source_carpark_type."""

    if not value:
        return None
    text = re.sub(r"\s+", " ", value.strip().upper())
    if "BASEMENT" in text:
        return "BASEMENT"
    if "MULTI-STOREY" in text or "MULTISTOREY" in text:
        if "SURFACE" in text:
            return "SURFACE_AND_MULTI_STOREY"
        return "MULTI_STOREY"
    if "SURFACE" in text:
        return "SURFACE"
    return "OTHER"


@dataclass(frozen=True)
class HdbCarpark:
    carpark_no: str
    address: str
    latitude: float
    longitude: float
    carpark_type: str | None
    parking_system_type: str | None = None
    short_term_parking: str | None = None
    free_parking: str | None = None
    night_parking: str | None = None
    carpark_decks: int | None = None
    source_carpark_type: str | None = None
    gantry_height_m: float | None = None
    basement_indicator: str | None = None
    source: str = SOURCE_NAME
    source_updated_at: datetime | None = None

    @property
    def is_sheltered(self) -> bool | None:
        normalised = normalize_carpark_type(self.carpark_type)
        if normalised in {"MULTI_STOREY", "BASEMENT", "SURFACE_AND_MULTI_STOREY"}:
            return True
        if normalised == "SURFACE":
            return False
        return None


@dataclass(frozen=True)
class CarparkCandidate:
    carpark: HdbCarpark
    haversine_distance_m: float
    relevance_score: float
    match_type: str


class HdbCarparkStore:
    """Read the refreshed official fixture; the database mirrors it for persistence."""

    _carparks: list[HdbCarpark] | None = None

    @classmethod
    def load(cls) -> list[HdbCarpark]:
        if cls._carparks is not None:
            return cls._carparks
        rows = json.loads(FIXTURE.read_text()) if FIXTURE.exists() else []
        result: list[HdbCarpark] = []
        seen: set[str] = set()
        for row in rows:
            carpark_no = str(row.get("carpark_no") or row.get("car_park_no") or "").strip()
            address = str(row.get("address") or "").strip()
            if not carpark_no or not address or carpark_no in seen:
                continue
            try:
                latitude = float(row["latitude"])
                longitude = float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                continue
            if not (math.isfinite(latitude) and math.isfinite(longitude)):
                continue
            seen.add(carpark_no)
            result.append(
                HdbCarpark(
                    carpark_no=carpark_no,
                    address=address,
                    latitude=latitude,
                    longitude=longitude,
                    carpark_type=normalize_carpark_type(row.get("carpark_type")),
                    source_carpark_type=row.get("source_carpark_type") or row.get("carpark_type"),
                    parking_system_type=row.get("parking_system_type") or None,
                    short_term_parking=row.get("short_term_parking") or None,
                    free_parking=row.get("free_parking") or None,
                    night_parking=row.get("night_parking") or None,
                    carpark_decks=_optional_int(row.get("carpark_decks")),
                    gantry_height_m=_optional_float(row.get("gantry_height_m")),
                    basement_indicator=row.get("basement_indicator") or None,
                    source=row.get("source", SOURCE_NAME),
                    source_updated_at=_parse_datetime(row.get("source_updated_at")),
                )
            )
        cls._carparks = result
        return result

    @classmethod
    def is_usable(cls) -> bool:
        return len(cls.load()) > 100

    @classmethod
    def nearby(
        cls,
        latitude: float,
        longitude: float,
        max_distance_m: float,
        limit: int = 5,
        address: str | None = None,
    ) -> list[CarparkCandidate]:
        """Use Haversine only to shortlist; routed walking is applied by the scorer."""

        listing_tokens = _address_tokens(address or "")
        candidates: list[CarparkCandidate] = []
        for carpark in cls.load():
            distance = haversine_m(latitude, longitude, carpark.latitude, carpark.longitude)
            if distance > max_distance_m:
                continue
            # Address overlap is only an inference signal; geocoded coordinates remain primary.
            overlap = len(listing_tokens.intersection(_address_tokens(carpark.address)))
            relevance = max(0.0, 100.0 - distance / max_distance_m * 70.0 + min(20.0, overlap * 10.0))
            candidates.append(
                CarparkCandidate(
                    carpark=carpark,
                    haversine_distance_m=distance,
                    relevance_score=round(min(100.0, relevance), 1),
                    match_type="NEAREST_GEOGRAPHIC_CANDIDATE",
                )
            )
        return sorted(candidates, key=lambda c: (-c.relevance_score, c.haversine_distance_m))[:limit]

    @classmethod
    def nearest(cls, latitude: float, longitude: float, max_distance_m: float) -> tuple[HdbCarpark, float] | None:
        candidates = cls.nearby(latitude, longitude, max_distance_m, limit=1)
        return (candidates[0].carpark, candidates[0].haversine_distance_m) if candidates else None

    @classmethod
    def reset_cache(cls) -> None:
        cls._carparks = None


def _address_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[A-Z0-9]+", value.upper()) if len(token) >= 2}


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
