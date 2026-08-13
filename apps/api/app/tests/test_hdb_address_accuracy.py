"""Canonical HDB-address cases that protect geocoding and comparable joins."""

from app.utils.hdb_address import canonical_hdb_address_key


def test_country_suffix_does_not_change_hdb_address_identity() -> None:
    assert canonical_hdb_address_key("Blk 123 Bishan St 12, Singapore 570123") == (
        "123",
        "BISHAN STREET 12",
    )
