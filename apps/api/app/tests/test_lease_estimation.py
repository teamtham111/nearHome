from datetime import date
from uuid import uuid4

from app.adapters.base import TransactionRecord
from app.domain.models import ConfirmedListing
from app.engines.fair_price_comparables import ComparableConfig, select_comparables
from app.services.lease_estimation import (
    LeaseEvidenceCache,
    estimate_remaining_lease,
    normalize_hdb_address,
    parse_lease_months,
)


def listing(address: str = "123 Bishan Street 12", **kwargs) -> ConfirmedListing:
    return ConfirmedListing(
        listing_id=uuid4(),
        session_id=uuid4(),
        display_name="test",
        asking_price=500_000,
        floor_area_sqm=90,
        address=address,
        flat_type="4 ROOM",
        **kwargs,
    )


def record(identifier: str, month: str, lease_months: int, street: str = "BISHAN ST 12") -> TransactionRecord:
    return TransactionRecord(
        transaction_id=identifier,
        transaction_month=month,
        town="BISHAN",
        flat_type="4 ROOM",
        block="123",
        street=street,
        storey_range="01 TO 03",
        floor_area_sqm=90,
        flat_model="Model A",
        lease_commencement=1990,
        remaining_lease=round(lease_months / 12, 2),
        resale_price=500_000,
        price_per_sqm=5555,
        remaining_lease_months=lease_months,
    )


def test_parses_lease_into_months():
    assert parse_lease_months("61 years 04 months") == 736


def test_same_block_uses_recent_median_expiry_and_as_of_month():
    records = [
        record("old", "2024-12", 751),
        record("new-1", "2025-01", 750),
        record("new-2", "2025-02", 749),
    ]
    result = estimate_remaining_lease(listing(), records, date(2026, 8, 3))
    assert result.source == "hdb_same_block_transactions"
    assert result.remaining_lease_months == 731
    assert result.evidence is not None
    assert result.evidence.transaction_count == 3
    assert result.estimated_expiry_date == "2087-07"
    assert result.as_of_date == "2026-08-03"


def test_conflicting_expiry_months_lower_confidence():
    result = estimate_remaining_lease(
        listing(),
        [record("a", "2025-01", 732), record("b", "2025-02", 680)],
        date(2026, 8, 3),
    )
    assert result.confidence == "low"
    assert result.warning
    assert result.evidence and result.evidence.disagreement_months > 24


def test_commencement_fallback_has_no_false_month_precision():
    result = estimate_remaining_lease(
        listing(lease_commencement_year=1992), [], date(2026, 8, 3)
    )
    assert result.source == "hdb_lease_commencement"
    assert result.remaining_lease_months == 773
    assert result.display_value == "Estimated remaining lease: About 64 years"
    assert result.warning and "month-level" in result.warning


def test_listing_only_is_unverified_and_approximate():
    result = estimate_remaining_lease(
        listing(remaining_lease_months=756, remaining_lease_source="listing_unverified"),
        [],
        date(2026, 8, 3),
    )
    assert result.source == "listing_unverified"
    assert result.confidence == "low"
    assert result.display_value == "Listing states: About 63 years"
    assert result.warning


def test_wrong_block_does_not_match_and_unavailable_is_structured():
    result = estimate_remaining_lease(
        listing("124 Bishan Street 12"), [record("x", "2025-01", 732)], date(2026, 8, 3)
    )
    assert result.source == "unavailable"
    assert result.remaining_lease_months is None
    assert result.confidence == "unavailable"
    assert result.is_estimated is False


def test_normalization_handles_block_letters_and_street_aliases():
    assert normalize_hdb_address(" Block 123-A  Bishan St 12 ")[0:2] == (
        "123A",
        "BISHAN STREET 12",
    )
    assert normalize_hdb_address("123 A Bishan Street 12")[0:2] == (
        "123A",
        "BISHAN STREET 12",
    )


def test_normalization_removes_postal_punctuation_and_curly_apostrophes():
    assert normalize_hdb_address("183 Jelebu Road 670183")[0:2] == ("183", "JELEBU ROAD")
    assert normalize_hdb_address("805 King George’s Avenue")[0:2] == (
        "805",
        "KING GEORGES AVENUE",
    )
    assert normalize_hdb_address("805 KING GEORGE'S AVE")[0:2] == (
        "805",
        "KING GEORGES AVENUE",
    )
    assert normalize_hdb_address(" 406, Sin   Ming Ave.  ")[0:2] == (
        "406",
        "SIN MING AVENUE",
    )


def test_normalization_expands_common_suffixes_without_partial_matching():
    assert normalize_hdb_address("211 Jurong East St 21")[0:2] == (
        "211",
        "JURONG EAST STREET 21",
    )
    assert normalize_hdb_address("337 Jurong East Avenue 1")[0:2] == (
        "337",
        "JURONG EAST AVENUE 1",
    )
    assert normalize_hdb_address("637 Veerasamy Rd")[0:2] == ("637", "VEERASAMY ROAD")
    assert normalize_hdb_address("183 Jelebu Road")[0:2] != normalize_hdb_address("184 Jelebu Road")[0:2]


def test_cache_reuses_expiry_evidence_but_recomputes_current_month():
    cache = LeaseEvidenceCache()
    records = [record("x", "2025-01", 732)]
    first = estimate_remaining_lease(listing(), records, date(2026, 1, 1), cache)
    second = estimate_remaining_lease(listing(), records, date(2027, 1, 1), cache)
    assert len(cache) == 1
    assert first.remaining_lease_months - second.remaining_lease_months == 12


def test_fair_price_uses_canonical_months_over_legacy_years():
    target = listing(remaining_lease_years=80, remaining_lease_months=600)
    selection = select_comparables(
        [record("x", "2025-01", 600)],
        target,
        town=None,
        valuation_date=date(2026, 8, 3),
        config=ComparableConfig(
            window_months=24,
            min_comparables=1,
            target_comparables=1,
            area_tolerances=(0.1,),
            lease_tolerances=(1.0,),
        ),
    )
    assert selection is not None
    assert selection.rows[0]["remaining_lease_months"] == 600
