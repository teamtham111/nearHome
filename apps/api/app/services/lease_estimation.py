"""Canonical, source-aware remaining-lease estimation for HDB listings."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from statistics import median
from typing import Literal

from app.adapters.base import TransactionRecord
from app.domain.models import ConfirmedListing
from app.utils.hdb_address import canonical_hdb_address_key, canonical_hdb_parts, normalize_hdb_address

__all__ = [
    "LeaseEvidenceCache",
    "RemainingLeaseEstimate",
    "estimate_remaining_lease",
    "normalize_hdb_address",
    "parse_lease_months",
]

LeaseSource = Literal[
    "official_exact",
    "hdb_same_block_transactions",
    "hdb_lease_commencement",
    "listing_unverified",
    "unavailable",
]
LeaseConfidence = Literal["high", "medium", "low", "unavailable"]

@dataclass(frozen=True)
class LeaseEvidence:
    transaction_count: int = 0
    estimated_expiry_dates: list[str] | None = None
    source_record_ids: list[str] | None = None
    disagreement_months: int | None = None


@dataclass(frozen=True)
class RemainingLeaseEstimate:
    remaining_lease_months: int | None
    display_value: str
    lease_commencement_year: int | None
    estimated_expiry_date: str | None
    source: LeaseSource
    confidence: LeaseConfidence
    is_estimated: bool
    as_of_date: str
    matched_block: str | None = None
    matched_street: str | None = None
    evidence: LeaseEvidence | None = None
    warning: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _LeaseBlockEvidence:
    block: str
    street: str
    expiry_months: tuple[int, ...]
    record_ids: tuple[str, ...]
    commencement_years: tuple[int, ...]


class LeaseEvidenceCache:
    """Cache block-level expiry evidence; current months remain date-dependent."""

    def __init__(self) -> None:
        self._items: dict[str, _LeaseBlockEvidence] = {}

    def get(self, key: str) -> _LeaseBlockEvidence | None:
        return self._items.get(key)

    def put(self, key: str, value: _LeaseBlockEvidence) -> None:
        self._items[key] = value

    def __len__(self) -> int:
        return len(self._items)


def parse_lease_months(value: object) -> int | None:
    """Parse HDB values such as ``61 years 4 months`` into total months."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        months = round(float(value) * 12)
        return months if 0 <= months <= 99 * 12 else None
    text = str(value).upper().strip()
    years_match = re.search(r"(\d+(?:\.\d+)?)\s*YEARS?", text)
    months_match = re.search(r"(\d+)\s*MONTHS?", text)
    if not years_match and not months_match:
        return None
    years = float(years_match.group(1)) if years_match else 0.0
    months = int(months_match.group(1)) if months_match else 0
    total = round(years * 12) + months
    return total if 0 <= total <= 99 * 12 else None


def estimate_remaining_lease(
    listing: ConfirmedListing,
    records: list[TransactionRecord],
    as_of: date | None = None,
    cache: LeaseEvidenceCache | None = None,
) -> RemainingLeaseEstimate:
    """Resolve lease using exact official evidence, then deterministic fallbacks."""
    as_of = as_of or date.today()
    as_of_month = _month_index(as_of.year, as_of.month)
    address_key = canonical_hdb_address_key(listing.address)
    block, street = address_key if address_key else (None, None)
    cache_key = f"{block}|{street}" if block and street else ""

    # A server-verified exact value is the only listing field that outranks
    # official HDB evidence. User/listing text is intentionally considered
    # only after the official fallbacks below.
    if (
        listing.remaining_lease_months is not None
        and listing.remaining_lease_months >= 0
        and listing.remaining_lease_source == "official_exact"
    ):
        months = int(listing.remaining_lease_months)
        return _result(
            months,
            as_of,
            source="listing_unverified" if listing.remaining_lease_source == "listing_unverified" else "official_exact",
            confidence="low" if listing.remaining_lease_source == "listing_unverified" else "high",
            estimated=listing.remaining_lease_source != "official_exact",
            commencement=listing.lease_commencement_year,
            block=block,
            street=street,
            warning="Listing-stated lease has not been independently verified."
            if listing.remaining_lease_source == "listing_unverified"
            else None,
        )

    evidence = cache.get(cache_key) if cache is not None and cache_key else None
    if evidence is None and block and street:
        evidence = _build_block_evidence(records, block, street, as_of_month)
        if cache is not None and cache_key and evidence is not None:
            cache.put(cache_key, evidence)

    if evidence and evidence.expiry_months:
        expiry = int(round(median(evidence.expiry_months)))
        months = max(0, expiry - as_of_month)
        disagreement = max(evidence.expiry_months) - min(evidence.expiry_months)
        confidence: LeaseConfidence = "high" if disagreement <= 6 else "medium" if disagreement <= 24 else "low"
        warning = (
            None
            if disagreement <= 24
            else "Same-block transactions imply materially different lease-expiry months."
        )
        return _result(
            months,
            as_of,
            source="hdb_same_block_transactions",
            confidence=confidence,
            estimated=True,
            commencement=_single_value(evidence.commencement_years),
            expiry=expiry,
            block=block,
            street=street,
            evidence=LeaseEvidence(
                transaction_count=len(evidence.expiry_months),
                estimated_expiry_dates=[_month_date(value) for value in evidence.expiry_months],
                source_record_ids=list(evidence.record_ids),
                disagreement_months=disagreement,
            ),
            warning=warning,
            precise=True,
        )

    commencement = listing.lease_commencement_year
    if commencement and 1900 <= commencement <= as_of.year:
        expiry = _month_index(commencement + 99, 1)
        months = max(0, expiry - as_of_month)
        return _result(
            months,
            as_of,
            source="hdb_lease_commencement",
            confidence="medium",
            estimated=True,
            commencement=commencement,
            expiry=expiry,
            block=block,
            street=street,
            warning="Only the commencement year is known; month-level precision was not inferred.",
            precise=False,
        )

    if listing.remaining_lease_months is not None and listing.remaining_lease_months >= 0:
        return _result(
            int(listing.remaining_lease_months),
            as_of,
            source="listing_unverified",
            confidence="low",
            estimated=True,
            commencement=listing.lease_commencement_year,
            block=block,
            street=street,
            warning="Listing-stated lease has not been independently verified.",
            precise=False,
        )

    return RemainingLeaseEstimate(
        remaining_lease_months=None,
        display_value="Remaining lease: Unable to determine",
        lease_commencement_year=None,
        estimated_expiry_date=None,
        source="unavailable",
        confidence="unavailable",
        is_estimated=False,
        as_of_date=as_of.isoformat(),
        matched_block=block,
        matched_street=street,
        warning="No reliable HDB lease match was found.",
    )


def _build_block_evidence(
    records: list[TransactionRecord], block: str, street: str, as_of_month: int
) -> _LeaseBlockEvidence | None:
    matching = []
    for record in records:
        if canonical_hdb_parts(record.block, record.street) != (block, street):
            continue
        transaction_month = _parse_month(record.transaction_month)
        lease_months = (
            record.remaining_lease_months
            if record.remaining_lease_months is not None
            else parse_lease_months(record.remaining_lease)
        )
        if transaction_month is None or lease_months is None or transaction_month > as_of_month:
            continue
        matching.append((transaction_month, transaction_month + lease_months, record))
    if not matching:
        return None
    matching.sort(key=lambda item: item[0], reverse=True)
    recent = matching[:12]
    return _LeaseBlockEvidence(
        block=block,
        street=street,
        expiry_months=tuple(item[1] for item in recent),
        record_ids=tuple(item[2].transaction_id for item in recent),
        commencement_years=tuple(item[2].lease_commencement for item in recent if item[2].lease_commencement > 0),
    )


def _result(
    months: int,
    as_of: date,
    *,
    source: LeaseSource,
    confidence: LeaseConfidence,
    estimated: bool,
    commencement: int | None,
    block: str | None,
    street: str | None,
    expiry: int | None = None,
    evidence: LeaseEvidence | None = None,
    warning: str | None = None,
    precise: bool = True,
) -> RemainingLeaseEstimate:
    years = months // 12
    remainder = months % 12
    if precise and remainder:
        display = f"Estimated remaining lease: About {years} years {remainder} months"
    else:
        display = f"Estimated remaining lease: About {round(months / 12):d} years"
    if source == "listing_unverified":
        display = display.replace("Estimated remaining lease:", "Listing states:")
    return RemainingLeaseEstimate(
        remaining_lease_months=months,
        display_value=display,
        lease_commencement_year=commencement,
        estimated_expiry_date=_month_date(expiry) if expiry is not None else None,
        source=source,
        confidence=confidence,
        is_estimated=estimated,
        as_of_date=as_of.isoformat(),
        matched_block=block,
        matched_street=street,
        evidence=evidence,
        warning=warning,
    )


def _parse_month(value: str) -> int | None:
    match = re.fullmatch(r"(\d{4})-(\d{1,2})", str(value).strip())
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return _month_index(year, month) if 1 <= month <= 12 else None


def _month_index(year: int, month: int) -> int:
    return year * 12 + month - 1


def _month_date(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _single_value(values: tuple[int, ...]) -> int | None:
    unique = set(values)
    return next(iter(unique)) if len(unique) == 1 else None
