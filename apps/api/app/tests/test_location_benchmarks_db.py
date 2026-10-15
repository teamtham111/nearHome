"""Database tests for block feature reuse and published benchmark versions.

Require a disposable PostgreSQL test database (the URL must contain "test"),
because they replace the block registry and benchmark tables. All blocks are
synthetic test fixtures, never presented as real rankings.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    "test" not in os.getenv("DATABASE_URL", ""),
    reason="Requires a disposable PostgreSQL test database (DATABASE_URL containing 'test')",
)


@pytest.fixture()
def db():
    from sqlalchemy import delete

    from app.db.session import SessionLocal
    from app.models.orm import (
        BlockLocationFeatureORM,
        HdbBlockORM,
        LocationBenchmarkVersionORM,
    )

    session = SessionLocal()

    def clean() -> None:
        session.execute(delete(LocationBenchmarkVersionORM))
        session.execute(delete(BlockLocationFeatureORM))
        session.execute(delete(HdbBlockORM))
        session.commit()

    clean()
    yield session
    session.rollback()
    clean()
    session.close()


def _registry(db, blocks: list[tuple[str, str, str | None]]) -> None:
    """blocks: (block_key, town_code, postal_code)."""
    from app.repositories.block_repository import BlockRepository

    rows = []
    for key, town, postal in blocks:
        block, _, street = key.partition("|")
        rows.append(
            {
                "block_key": key,
                "block": block,
                "street": street,
                "residential": True,
                "town_code": town,
                "town_name": f"TOWN {town}",
                "postal_code": postal,
                "latitude": 1.35,
                "longitude": 103.85,
            }
        )
    BlockRepository(db).upsert_registry(rows, {"registry_version": "test", "source_updated_at": "test"})
    db.commit()


def _schools_payload(within_1km: int) -> dict:
    return {"score_status": "calculated", "schools_within_1km": within_1km, "nearest_school_distance_km": 0.5}


def _store(db, key: str, within_1km: int | None) -> None:
    from app.repositories.block_repository import BlockRepository
    from app.services.block_features import BlockFeatureService

    payload = _schools_payload(within_1km) if within_1km is not None else {"score_status": "unavailable"}
    BlockFeatureService(BlockRepository(db)).get_or_compute(
        key, "schools", (1.35, 103.85), "test", lambda _la, _lo: payload, computed_for="backfill", reuse=False
    )
    db.commit()


def test_units_in_one_block_reuse_one_calculation(db) -> None:
    from app.repositories.block_repository import BlockRepository
    from app.services.block_features import BlockFeatureService

    calls: list[tuple[float, float]] = []

    def compute(lat: float, lng: float) -> dict:
        calls.append((lat, lng))
        return _schools_payload(3)

    service = BlockFeatureService(BlockRepository(db))
    first = service.get_or_compute("10|TEST STREET", "schools", (1.30, 103.80), "hdb_registry_onemap", compute)
    # A second unit geocoded slightly differently still reuses the block's
    # stored result and its original starting point.
    second = service.get_or_compute("10|TEST STREET", "schools", (1.3001, 103.8001), "listing_geocode", compute)
    other_block = service.get_or_compute("10|OTHER STREET", "schools", (1.40, 103.90), "listing_geocode", compute)
    assert calls == [(1.30, 103.80), (1.40, 103.90)]
    assert not first.reused and second.reused and not other_block.reused
    assert (second.origin_latitude, second.origin_longitude) == (1.30, 103.80)


def test_listing_resolves_to_block_by_postal_code_before_address(db) -> None:
    from app.repositories.block_repository import BlockRepository
    from app.services.block_features import resolve_block

    _registry(db, [("406|ANG MO KIO AVENUE 10", "AMK", "560406"), ("1|BEACH ROAD", "KWN", "190001")])
    identity = resolve_block(
        BlockRepository(db), address="Blk 406 AMK Ave 10", geocode_postal_code="560406", geocode_latitude=1.0
    )
    assert identity is not None and identity.block_key == "406|ANG MO KIO AVENUE 10"
    assert identity.resolution == "postal_code" and identity.town_code == "AMK"
    assert identity.origin_source == "hdb_registry_onemap" and identity.origin_latitude == 1.35

    unregistered = resolve_block(
        BlockRepository(db), address="7 Unknown Road", geocode_latitude=1.2, geocode_longitude=103.7
    )
    assert unregistered is not None and not unregistered.in_registry
    assert unregistered.block_key == "7|UNKNOWN ROAD" and unregistered.origin_source == "listing_geocode"


def test_published_version_compares_nationally_and_within_town(db) -> None:
    from app.engines.location_benchmarks import comparison
    from app.services import location_benchmarks as svc

    town_a = [f"{i}|TOWN A STREET" for i in range(1, 5)]
    town_b = [f"{i}|TOWN B STREET" for i in range(1, 5)]
    _registry(db, [(k, "AAA", None) for k in town_a] + [(k, "BBB", None) for k in town_b])
    for count, key in enumerate(town_a, start=1):  # 1..4 schools
        _store(db, key, count)
    for count, key in enumerate(town_b, start=11):  # 11..14 schools
        _store(db, key, count)

    original = (comparison.NATIONAL_MIN_GROUP_SIZE, comparison.TOWN_MIN_GROUP_SIZE)
    svc.NATIONAL_MIN_GROUP_SIZE, svc.TOWN_MIN_GROUP_SIZE = 1, 1
    try:
        result = svc.build_and_publish(db, feature_sets=("schools",), allow_demo=True)
    finally:
        svc.NATIONAL_MIN_GROUP_SIZE, svc.TOWN_MIN_GROUP_SIZE = original
    assert result["published"]

    best_in_a = svc.benchmarks_for_block(db, town_a[-1])
    entry = next(m for m in best_in_a["measurements"] if m["key"] == "schools_within_1km")
    # 4 schools: nationally better than the 3 other town-A blocks only (3/7).
    assert entry["national"]["better_than_pct"] == 42
    assert entry["national"]["group_size"] == 7
    # Within town A it has more than all 3 other town-A blocks.
    assert entry["town"]["better_than_pct"] == 100
    assert entry["town"]["group_size"] == 3 and entry["town"]["town_name"] == "TOWN AAA"


def test_missing_blocks_reduce_coverage_and_rebuild_supersedes_old_version(db) -> None:
    from app.repositories.block_repository import BlockRepository
    from app.services import location_benchmarks as svc

    keys = [f"{i}|COVERAGE STREET" for i in range(1, 7)]
    _registry(db, [(k, "CCC", None) for k in keys])
    _store(db, keys[0], 2)
    _store(db, keys[1], None)  # calculation failed: must stay unavailable

    first = svc.build_and_publish(db, feature_sets=("schools",), allow_demo=True)
    summary = first["summary"]["measurements"]["schools_within_1km"]
    assert summary["valid_blocks"] == 1 and summary["unavailable_blocks"] == 1
    assert summary["not_measured_blocks"] == 4
    assert summary["national_coverage"] == round(1 / 6, 4)
    assert summary["complete_national_benchmark"] is False

    failed = svc.benchmarks_for_block(db, keys[1])
    failed_entry = next(m for m in failed["measurements"] if m["key"] == "schools_within_1km")
    assert failed_entry["value"] is None and failed_entry["national"]["status"] == "unavailable"
    small = svc.benchmarks_for_block(db, keys[0])
    assert (
        next(m for m in small["measurements"] if m["key"] == "schools_within_1km")["national"]["status"] == "suppressed"
    )

    second = svc.build_and_publish(db, feature_sets=("schools",), allow_demo=True)
    repo = BlockRepository(db)
    assert str(repo.published_version().id) == second["version_id"]
    assert repo.get_version(first["version_id"]).status == "superseded"


def test_demo_data_is_never_published_by_default(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings
    from app.services import location_benchmarks as svc

    monkeypatch.setattr(settings, "demo_mode", True)
    _registry(db, [("1|DEMO STREET", "DDD", None)])
    with pytest.raises(svc.BenchmarkPublishError):
        svc.build_and_publish(db, feature_sets=("schools",))


def test_enrichment_calculates_once_per_block_and_keeps_unit_fields_separate(db, monkeypatch) -> None:
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from app.adapters.base import GeocodeResult
    from app.domain.enums import ComponentStatus
    from app.domain.transport_models import ComponentResult, build_rollup
    from app.main import app
    from app.services import enrichment_service as enrichment_module
    from app.services.enrichment_service import EnrichmentService

    _registry(db, [("123|BISHAN STREET 12", "BH", "570123")])

    class StubGeocoder:
        def geocode(self, address: str) -> GeocodeResult:
            # Each unit geocodes to a slightly different point; the block
            # registry coordinate must still be the shared starting point.
            offset = 0.0001 if "#05" in address else 0.0
            return GeocodeResult(
                1.351 + offset,
                103.848,
                address,
                "570123",
                "BISHAN",
                "123",
                "BISHAN STREET 12",
                "TEST",
                "OFFICIAL",
                datetime.now(UTC),
            )

    origins: list[tuple[float, float]] = []

    def fake_pt(lat: float, lng: float, _routing):
        origins.append((lat, lng))
        component = ComponentResult(
            "bus_coverage",
            {"direct_corridors": 5, "one_transfer_new_corridors": 2},
            70.0,
            1.0,
            ComponentStatus.CALCULATED,
            "test",
        )
        return build_rollup([component], 0.6)

    monkeypatch.setattr(enrichment_module, "get_geocoding_adapter", lambda: StubGeocoder())
    monkeypatch.setattr(enrichment_module, "compute_public_transport_model", fake_pt)

    client = TestClient(app)
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    client.put(
        f"/api/v1/sessions/{session_id}/buyer-profile",
        json={
            "max_budget": 800000,
            "priorities": [{"priority_type": "AFFORDABILITY"}],
            "main_transport_mode": "MAINLY_PUBLIC_TRANSPORT",
            "hard_requirements": [],
            "important_locations": [],
        },
    )
    for price, address in [(650000, "123 Bishan St 12 #05"), (690000, "Blk 123 Bishan Street 12 #11")]:
        resp = client.post(
            f"/api/v1/sessions/{session_id}/listings/manual",
            json={"asking_price": price, "floor_area_sqm": 91, "address": address, "flat_type": "4 ROOM"},
        )
        assert resp.status_code == 201

    from uuid import UUID

    EnrichmentService(db).run_session_enrichment(UUID(session_id), simulate_delay=False)
    db.commit()

    assert origins == [(1.35, 103.85)]  # one calculation, from the registry coordinate
    from app.models.orm import ConfirmedListingORM, EnrichedFieldORM

    listings = db.query(ConfirmedListingORM).filter(ConfirmedListingORM.session_id == UUID(session_id)).all()
    assert {listing.block_key for listing in listings} == {"123|BISHAN STREET 12"}
    fields = (
        db.query(EnrichedFieldORM)
        .filter(
            EnrichedFieldORM.listing_id.in_([l.id for l in listings]), EnrichedFieldORM.field_name == "public_transport"
        )
        .all()
    )
    assert sorted(f.value_json["block_feature"]["reused"] for f in fields) == [False, True]
    # Fair price stays unit-specific: one row per listing, not shared.
    fair = (
        db.query(EnrichedFieldORM)
        .filter(EnrichedFieldORM.listing_id.in_([l.id for l in listings]), EnrichedFieldORM.field_name == "fair_price")
        .count()
    )
    assert fair == 2
    client.delete(f"/api/v1/sessions/{session_id}")
