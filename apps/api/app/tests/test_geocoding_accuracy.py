"""Deterministic OneMap geocoding accuracy guards at the adapter boundary."""

from __future__ import annotations

from app.adapters.live.onemap import LiveOneMapAdapter


def _result(
    *,
    latitude: str = "1.3521",
    longitude: str = "103.8498",
    block: str = "123",
    street: str = "Bishan Street 12",
) -> dict:
    return {
        "LATITUDE": latitude,
        "LONGITUDE": longitude,
        "BLK_NO": block,
        "ROAD_NAME": street,
    }


def test_onemap_match_accepts_same_hdb_block_and_canonical_street() -> None:
    assert LiveOneMapAdapter._is_usable_match(_result(), "Blk 123 Bishan St 12, Singapore")


def test_onemap_match_rejects_conflicting_hdb_block_or_street() -> None:
    assert not LiveOneMapAdapter._is_usable_match(_result(block="124"), "123 Bishan Street 12")
    assert not LiveOneMapAdapter._is_usable_match(_result(street="Tampines Avenue 5"), "123 Bishan Street 12")


def test_onemap_match_rejects_reversed_or_out_of_singapore_coordinates() -> None:
    assert not LiveOneMapAdapter._is_usable_match(
        _result(latitude="103.8498", longitude="1.3521"), "123 Bishan Street 12"
    )
    assert not LiveOneMapAdapter._is_usable_match(_result(latitude="0", longitude="0"), "123 Bishan Street 12")
