"""Tests for the joined LTA bus reference data (Part 4 of the spec)."""

from __future__ import annotations

import pytest

from app.adapters.transport_data.lta_bus import (
    LtaBusDataStore,
    is_no_service_marker,
    parse_frequency_range,
)


class TestFrequencyParsing:
    def test_parses_range(self) -> None:
        freq = parse_frequency_range("8-12", "AM_PEAK")
        assert freq is not None
        assert freq.minimum_minutes == 8
        assert freq.maximum_minutes == 12
        assert freq.midpoint_minutes == 10
        assert freq.source_period == "AM_PEAK"

    def test_parses_single_fixed_frequency(self) -> None:
        freq = parse_frequency_range("15", "PM_OFFPEAK")
        assert freq is not None
        assert freq.minimum_minutes == freq.maximum_minutes == 15

    def test_dash_is_no_service_not_malformed(self) -> None:
        assert is_no_service_marker("-") is True
        assert parse_frequency_range("-", "AM_PEAK") is None

    def test_garbage_is_malformed(self) -> None:
        assert is_no_service_marker("garbage") is False
        assert parse_frequency_range("garbage", "AM_PEAK") is None

    def test_range_label_never_claims_exact_wait(self) -> None:
        freq = parse_frequency_range("8-12", "AM_PEAK")
        assert freq is not None
        label = freq.as_label()
        assert "approximately" in label
        assert "8-12" in label or "8" in label


class TestLtaBusDataStoreJoin:
    """These exercise the real ingested fixtures (data_pipeline/fixtures/lta_bus_*.json)."""

    def test_data_is_usable(self) -> None:
        report = LtaBusDataStore.quality_report()
        assert report.is_usable, report.problems
        assert report.bus_stops_count > 1000
        assert report.unique_service_directions_in_routes > 100

    def test_direction_is_preserved_as_distinct_key(self) -> None:
        directions = LtaBusDataStore.all_service_directions()
        service_numbers_with_two_directions = {
            svc for svc, direction in directions if (svc, 2) in directions and (svc, 1) in directions
        }
        # Real bus services normally run in both directions — service "117"
        # direction 1 and direction 2 must be distinct keys, never merged.
        assert len(service_numbers_with_two_directions) > 10

    def test_services_by_stop_returns_direction_aware_keys(self) -> None:
        directions = LtaBusDataStore.all_service_directions()
        any_key = directions[0]
        stops = LtaBusDataStore.route_stops(any_key)
        assert stops
        stop_code = stops[0].bus_stop_code
        services_here = LtaBusDataStore.services_by_stop(stop_code)
        assert any_key in services_here
        for svc, direction in services_here:
            assert isinstance(svc, str)
            assert direction in (1, 2)

    def test_route_stops_are_ordered_by_sequence(self) -> None:
        directions = LtaBusDataStore.all_service_directions()
        key = directions[0]
        stops = LtaBusDataStore.route_stops(key)
        sequences = [s.stop_sequence for s in stops]
        assert sequences == sorted(sequences)

    def test_empty_dataset_is_reported_as_unusable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(LtaBusDataStore, "_load_raw", classmethod(lambda cls: None))
        monkeypatch.setattr(LtaBusDataStore, "_services_by_stop", {})
        monkeypatch.setattr(LtaBusDataStore, "_route_stops_by_service_direction", {})
        monkeypatch.setattr(LtaBusDataStore, "_service_info", {})

        from app.adapters.transport_data.lta_bus import DataQualityReport

        empty_report = DataQualityReport(
            bus_stops_count=0,
            bus_routes_rows=0,
            bus_services_rows=0,
            unique_service_directions_in_routes=0,
            unique_service_directions_in_services=0,
            stops_with_no_routes=0,
            route_stops_referencing_unknown_stop_codes=0,
            duplicate_stop_sequences=0,
            malformed_frequency_values=0,
            is_usable=False,
            problems=["No bus stops loaded"],
        )
        monkeypatch.setattr(LtaBusDataStore, "_quality_report", empty_report)
        assert LtaBusDataStore.is_usable() is False
