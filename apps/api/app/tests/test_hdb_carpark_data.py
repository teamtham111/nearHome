"""Acceptance tests for official HDB carpark data, availability and scoring."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.adapters.parking.coordinates import svy21_to_wgs84
from app.adapters.parking.hdb_availability import (
    AvailabilityProviderError,
    AvailabilityRecord,
    CarparkAvailabilityProvider,
    _parse_response,
)
from app.adapters.parking.hdb_carpark import CarparkCandidate, HdbCarpark, HdbCarparkStore, normalize_carpark_type
from app.engines.driving.parking_convenience import (
    _capacity_score,
    _historical_score,
    _walk_score,
    compute_parking_convenience,
)
from app.engines.transport_config import DRIVING_CONFIG
from app.tests.routing_helpers import FixedDurationRoutingProvider


def test_svy21_conversion_returns_latitude_then_longitude() -> None:
    latitude, longitude = svy21_to_wgs84(30314.7936, 31490.4942)
    assert abs(latitude - 1.301063) < 0.00002
    assert abs(longitude - 103.854118) < 0.00002
    assert 1.0 < latitude < 2.0
    assert 103.0 < longitude < 104.5


def test_static_fixture_preserves_source_type_and_missing_values() -> None:
    HdbCarparkStore.reset_cache()
    carpark = next(item for item in HdbCarparkStore.load() if item.carpark_no == "ACB")
    assert normalize_carpark_type("BASEMENT CAR PARK") == "BASEMENT"
    assert carpark.carpark_type == "BASEMENT"
    assert carpark.source_carpark_type == "BASEMENT CAR PARK"
    assert carpark.gantry_height_m == 1.8
    assert carpark.basement_indicator == "Y"


def test_static_fixture_deduplicates_carpark_numbers() -> None:
    HdbCarparkStore.reset_cache()
    records = HdbCarparkStore.load()
    numbers = [record.carpark_no for record in records]
    assert len(numbers) == len(set(numbers))


def test_availability_parser_keeps_missing_lots_null_and_marks_stale() -> None:
    retrieved_at = datetime(2026, 8, 2, 16, 0, tzinfo=UTC)
    records = _parse_response(
        {
            "items": [
                {
                    "timestamp": "2026-08-02T23:50:00+08:00",
                    "carpark_data": [
                        {
                            "carpark_number": "ACB",
                            "update_datetime": "2026-08-02T23:50:00+08:00",
                            "carpark_info": [
                                {"lot_type": "C", "total_lots": "100", "lots_available": "25"},
                                {"lot_type": "H", "total_lots": "10", "lots_available": "5"},
                            ],
                        },
                        {
                            "carpark_number": "MISSING",
                            "update_datetime": "2026-08-02T09:00:00",
                            "carpark_info": [{"lot_type": "C", "total_lots": None, "lots_available": None}],
                        },
                        {
                            "carpark_number": "ZERO",
                            "update_datetime": "2026-08-02T23:50:00+08:00",
                            "carpark_info": [{"lot_type": "C", "total_lots": 0, "lots_available": 0}],
                        },
                    ],
                }
            ]
        },
        retrieved_at,
    )
    assert records[0].availability_pct == 25.0
    assert records[0].status == "LIVE"
    assert records[1].lot_type == "H"
    assert records[1].availability_pct == 50.0
    assert records[2].availability_pct is None
    assert records[2].total_lots is None
    assert records[2].status == "STALE"
    assert records[3].availability_pct is None
    assert records[3].total_lots == 0

    missing_timestamp = _parse_response(
        {
            "items": [
                {
                    "carpark_data": [
                        {
                            "carpark_number": "NO_TIME",
                            "carpark_info": [{"lot_type": "C", "total_lots": 20, "lots_available": 5}],
                        }
                    ]
                }
            ]
        },
        retrieved_at,
    )
    assert missing_timestamp[0].status == "TIMESTAMP_UNAVAILABLE"
    assert missing_timestamp[0].timestamp_valid is False


def test_nearby_prefilter_returns_at_most_five_real_records() -> None:
    HdbCarparkStore.reset_cache()
    candidates = HdbCarparkStore.nearby(1.3010, 103.8541, 500, limit=5)
    assert 1 <= len(candidates) <= 5
    assert all(candidate.haversine_distance_m <= 500 for candidate in candidates)
    assert candidates == sorted(candidates, key=lambda item: (-item.relevance_score, item.haversine_distance_m))


def test_score_boundaries_and_insufficient_history_are_explicit() -> None:
    assert _walk_score(2.0, DRIVING_CONFIG) == 100.0
    assert _walk_score(8.1, DRIVING_CONFIG) == DRIVING_CONFIG.parking_walk_floor_score
    assert _capacity_score(0) is None
    assert _capacity_score(500) == 100.0
    assert _historical_score({"sample_size": 4, "median_weekday_availability_pct": 90}, 5) is None


def test_invalid_listing_coordinates_are_not_assessed() -> None:
    result = compute_parking_convenience(float("nan"), 103.8, FixedDurationRoutingProvider())
    assert result.score is None
    assert "invalid" in result.explanation.lower()


def test_availability_timeout_is_structured_as_provider_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(AvailabilityProviderError, match="temporarily unavailable"):
        CarparkAvailabilityProvider(client=client).fetch()
    client.close()


def test_parking_score_uses_live_data_as_evidence_without_treating_missing_as_zero(monkeypatch) -> None:
    primary = HdbCarpark(
        "TEST1",
        "TEST BLOCK",
        1.3001,
        103.8001,
        "MULTI-STOREY CAR PARK",
        "ELECTRONIC PARKING",
        "WHOLE DAY",
        "NO",
        "YES",
        5,
    )
    monkeypatch.setattr(HdbCarparkStore, "is_usable", classmethod(lambda cls: True))
    monkeypatch.setattr(
        HdbCarparkStore,
        "nearby",
        classmethod(
            lambda cls, *args, **kwargs: [
                CarparkCandidate(primary, 20.0, 95.0, "NEAREST_GEOGRAPHIC_CANDIDATE")
            ]
        ),
    )

    class LiveProvider:
        def fetch(self):
            return []

    result = compute_parking_convenience(
        1.3000,
        103.8000,
        FixedDurationRoutingProvider(minutes=3.0),
        availability_provider=LiveProvider(),  # type: ignore[arg-type]
        history_lookup=lambda carpark_no, lot_type: {"sample_size": 4, "median_weekday_availability_pct": 90},
    )
    assert result.score is not None
    assert result.value["availability_status"] == "NOT_COVERED"
    assert result.value["primary_carpark"]["availability"]["status"] == "NOT_COVERED"
    assert result.value["typical_availability"]["status"] == "INSUFFICIENT_HISTORY"
    assert result.value["subscores"]["capacity"]["included"] is False
    assert result.value["subscores"]["typical_availability"]["included"] is False
    repeat = compute_parking_convenience(
        1.3000,
        103.8000,
        FixedDurationRoutingProvider(minutes=3.0),
        availability_provider=LiveProvider(),  # type: ignore[arg-type]
        history_lookup=lambda carpark_no, lot_type: {"sample_size": 4, "median_weekday_availability_pct": 90},
    )
    assert repeat.explanation == result.explanation


def test_current_available_lots_do_not_change_parking_score(monkeypatch) -> None:
    primary = HdbCarpark(
        "TEST2", "TEST BLOCK", 1.3001, 103.8001, "MULTI-STOREY CAR PARK",
        "ELECTRONIC PARKING", "WHOLE DAY", "NO", "YES", 5,
    )
    monkeypatch.setattr(HdbCarparkStore, "is_usable", classmethod(lambda cls: True))
    monkeypatch.setattr(
        HdbCarparkStore,
        "nearby",
        classmethod(lambda cls, *args, **kwargs: [CarparkCandidate(primary, 20.0, 95.0, "TEST")]),
    )

    class Availability:
        def __init__(self, available: int):
            self.available = available

        def fetch(self):
            return [
                AvailabilityRecord(
                    "TEST2", "C", 200, self.available, self.available / 2,
                    datetime(2026, 8, 3, tzinfo=UTC), "LIVE", "test",
                )
            ]

    low = compute_parking_convenience(
        1.3000, 103.8000, FixedDurationRoutingProvider(minutes=3.0), availability_provider=Availability(10)
    )
    high = compute_parking_convenience(
        1.3000, 103.8000, FixedDurationRoutingProvider(minutes=3.0), availability_provider=Availability(190)
    )
    assert low.score == high.score
    assert low.value["primary_carpark"]["availability"]["available_lots"] == 10
    assert high.value["primary_carpark"]["availability"]["available_lots"] == 190
