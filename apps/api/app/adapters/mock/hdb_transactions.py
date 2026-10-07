"""HDB transaction fixture loader."""

from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter

from app.adapters.base import TransactionRecord
from app.adapters.provider_schemas.hdb import HdbTransactionSnapshotRow

_FIXTURE_PATH = Path(__file__).resolve().parents[5] / "data_pipeline" / "fixtures" / "hdb_transactions.json"
_TRANSACTION_ROWS_ADAPTER = TypeAdapter(list[HdbTransactionSnapshotRow])


class FixtureHDBTransactionsAdapter:
    provider = "FIXTURE_HDB"

    def __init__(self) -> None:
        self._records: list[TransactionRecord] | None = None
        self._by_town_type: dict[tuple[str, str], list[TransactionRecord]] = {}

    def _load(self) -> list[TransactionRecord]:
        if self._records is not None:
            return self._records
        if not _FIXTURE_PATH.exists():
            self._records = []
            return self._records
        raw = _TRANSACTION_ROWS_ADAPTER.validate_json(_FIXTURE_PATH.read_text())
        self._records = [
            TransactionRecord(
                transaction_id=r.transaction_id,
                transaction_month=r.transaction_month,
                town=r.town,
                flat_type=r.flat_type,
                block=r.block,
                street=r.street,
                storey_range=r.storey_range,
                floor_area_sqm=r.floor_area_sqm,
                flat_model=r.flat_model,
                lease_commencement=r.lease_commencement,
                remaining_lease=r.remaining_lease,
                resale_price=r.resale_price,
                price_per_sqm=r.price_per_sqm,
                remaining_lease_months=r.remaining_lease_months,
            )
            for r in raw
        ]
        # Index for faster comparable lookup on large ingested datasets
        self._by_town_type = {}
        for rec in self._records:
            key = (rec.town.upper(), rec.flat_type.upper())
            self._by_town_type.setdefault(key, []).append(rec)
        return self._records

    def find_comparables(
        self,
        town: str | None,
        flat_type: str,
        floor_area_sqm: float,
        area_tolerance: float = 0.15,
        lease_tolerance: float = 10.0,
        remaining_lease: float | None = None,
    ) -> list[TransactionRecord]:
        records = self._load()
        flat_upper = flat_type.upper()
        town_upper = (town or "").upper()

        if town_upper:
            candidates = self._by_town_type.get((town_upper, flat_upper), [])
        else:
            candidates = [r for r in records if r.flat_type.upper() == flat_upper]

        results: list[TransactionRecord] = []
        for r in candidates:
            area_diff = abs(r.floor_area_sqm - floor_area_sqm) / floor_area_sqm
            if area_diff > area_tolerance:
                continue
            if remaining_lease is not None and (
                r.remaining_lease is None or abs(r.remaining_lease - remaining_lease) > lease_tolerance
            ):
                continue
            results.append(r)
        return results

    def all_records(self) -> list[TransactionRecord]:
        return self._load()
