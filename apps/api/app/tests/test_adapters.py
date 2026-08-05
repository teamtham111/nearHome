"""Adapter unit tests."""

from uuid import uuid4

from app.adapters.mock.hdb_transactions import FixtureHDBTransactionsAdapter
from app.domain.enums import DataStatus
from app.domain.models import ConfirmedListing
from app.engines.fair_price import FairPriceEngine


def _listing(address: str, price: float, area: float, flat_type: str = "4 ROOM") -> ConfirmedListing:
    return ConfirmedListing(
        listing_id=uuid4(),
        session_id=uuid4(),
        display_name=address,
        asking_price=price,
        floor_area_sqm=area,
        address=address,
        flat_type=flat_type,
        remaining_lease_years=65.0,
    )


class TestHDBTransactions:
    def test_fixture_loads(self):
        adapter = FixtureHDBTransactionsAdapter()
        records = adapter.all_records()
        assert len(records) >= 3

    def test_find_bishan_comparables(self):
        adapter = FixtureHDBTransactionsAdapter()
        comps = adapter.find_comparables("BISHAN", "4 ROOM", 91.0)
        assert len(comps) >= 3


class TestFairPrice:
    def test_estimate_with_comparables(self, monkeypatch):
        listing = _listing("123 Bishan St 12", 680000, 91.0)
        result = FairPriceEngine.estimate(listing, "BISHAN")
        assert result.status == DataStatus.AVAILABLE
        assert result.central_estimate is not None
        assert len(result.comparables) >= 3
        assert result.method == "CATBOOST"
        assert result.value_gap_percentage is not None

    def test_insufficient_evidence(self, monkeypatch):
        listing = _listing("Unknown", 500000, 50.0, flat_type="EXECUTIVE")
        result = FairPriceEngine.estimate(listing, "UNKNOWN_TOWN")
        assert result.status == DataStatus.INSUFFICIENT_EVIDENCE
