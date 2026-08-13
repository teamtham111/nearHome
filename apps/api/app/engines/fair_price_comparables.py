"""Time-safe, explainable weighted comparable-sales valuation."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import cast

from app.adapters.base import TransactionRecord
from app.domain.models import ConfirmedListing
from app.utils.hdb_address import canonical_hdb_address_key, canonical_hdb_parts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComparableConfig:
    window_months: int = 24
    min_comparables: int = 3
    target_comparables: int = 8
    area_tolerances: tuple[float, ...] = (0.10, 0.10, 0.20, 0.25, 0.30, 0.35)
    lease_tolerances: tuple[float, ...] = (8.0, 12.0, 12.0, 20.0, 25.0, 35.0)
    recency_decay_months: float = 12.0
    lower_quantile: float = 0.15
    upper_quantile: float = 0.85
    # Buyer-facing evidence cap. `all_rows` remains complete for valuation
    # diagnostics; this only controls the contextual rows returned as `rows`.
    strongest_count: int = 10


DEFAULT_COMPARABLE_CONFIG = ComparableConfig()


def _listing_lease_months(listing: ConfirmedListing) -> int | None:
    if listing.remaining_lease_months is not None:
        return int(listing.remaining_lease_months)
    if listing.remaining_lease_years is not None:
        return round(float(listing.remaining_lease_years) * 12)
    return None


def _record_lease_months(record: TransactionRecord) -> int | None:
    if record.remaining_lease_months is not None:
        return int(record.remaining_lease_months)
    if record.remaining_lease is not None:
        return round(float(record.remaining_lease) * 12)
    return None


@dataclass(frozen=True)
class ComparableSelection:
    all_rows: list[dict]
    rows: list[dict]
    total_candidate_count: int
    eligible_comparable_count: int
    effective_weighted_count: float
    average_similarity: float
    median_transaction_age_months: float
    comparable_price_spread: float
    comparable_estimate: float
    comparable_price_per_sqm: float
    comparable_lower_bound: float
    comparable_upper_bound: float
    relaxation_level: int
    relaxed_rules: list[str]
    missing_feature_warnings: list[str]
    confidence: str
    confidence_reasons: list[str]
    filter_status: dict[str, object]
    filter_messages: list[str]
    warning_details: list[dict[str, str]]
    comparable_count_by_stage: dict[str, int]


def select_comparables(
    records: Iterable[TransactionRecord],
    listing: ConfirmedListing,
    town: str | None,
    valuation_date: date,
    config: ComparableConfig = DEFAULT_COMPARABLE_CONFIG,
    town_source: str | None = None,
) -> ComparableSelection | None:
    """Select only transactions strictly before the valuation month.

    Levels relax one rule at a time. A broad fallback is labelled explicitly
    and cannot receive high confidence merely because it has many rows.
    """

    all_records = _deduplicate_records(records)
    valuation_month = valuation_date.strftime("%Y-%m")
    lower_month = _shift_month(valuation_date.year, valuation_date.month, -config.window_months)
    historical = [
        record
        for record in all_records
        if lower_month <= record.transaction_month < valuation_month
        and record.floor_area_sqm > 0
        and record.resale_price > 0
    ]
    target_town = _normalise(town)
    target_flat_type = _flat_type_key(listing.flat_type)
    target_key = canonical_hdb_address_key(listing.address)
    target_block, target_street = target_key if target_key else (None, None)
    target_lease = _listing_lease_months(listing)
    if target_lease is None:
        return None

    selected: list[TransactionRecord] = []
    selected_level = len(config.area_tolerances) - 1
    comparable_count_by_stage: dict[str, int] = {}
    for level, (area_tolerance, lease_tolerance) in enumerate(
        zip(config.area_tolerances, config.lease_tolerances, strict=False)
    ):
        candidates = [
            record
            for record in historical
            if _flat_type_key(record.flat_type) == target_flat_type
            and abs(record.floor_area_sqm - listing.floor_area_sqm) / listing.floor_area_sqm <= area_tolerance
            and _record_lease_months(record) is not None
            and abs(_record_lease_months(record) - target_lease) <= lease_tolerance * 12
            and (level >= 4 or not target_town or _normalise(record.town) == target_town)
        ]
        comparable_count_by_stage[f"level_{level}"] = len(candidates)
        if len(candidates) >= config.target_comparables or (
            len(candidates) >= config.min_comparables and level == len(config.area_tolerances) - 1
        ):
            selected = candidates
            selected_level = level
            break

    if len(selected) < config.min_comparables:
        return None

    filter_status = _filter_status(
        listing,
        target_town,
        target_flat_type,
        selected,
        selected_level,
    )
    filter_messages = _filter_messages(filter_status, town_source)
    warning_details = _warning_details(filter_status, filter_messages)
    missing_warnings = [
        detail["message"]
        for detail in warning_details
        if detail["code"] in {"TOWN_UNAVAILABLE", "FLAT_MODEL_UNAVAILABLE", "STOREY_NOT_PROVIDED"}
    ]

    weighted: list[dict] = []
    for record in selected:
        age_months = _age_months(record.transaction_month, valuation_date)
        components = _similarity_components(
            record,
            listing,
            target_town,
            target_block,
            target_street,
            target_lease,
            config.recency_decay_months,
            valuation_date,
        )
        similarity = math.prod(components.values())
        weight = max(0.0001, similarity)
        weighted.append(
            _record_dict(
                record,
                age_months=age_months,
                weight=weight,
                similarity=similarity,
                components=components,
            )
        )

    total_weight = sum(row["weight"] for row in weighted)
    squared_weight = sum(row["weight"] ** 2 for row in weighted)
    effective_count = total_weight**2 / squared_weight if squared_weight else 0.0
    ppsm_pairs = [(row["price_per_sqm"], row["weight"]) for row in weighted]
    central_ppsm = _weighted_quantile(ppsm_pairs, 0.5)
    lower_ppsm = _weighted_quantile(ppsm_pairs, config.lower_quantile)
    upper_ppsm = _weighted_quantile(ppsm_pairs, config.upper_quantile)
    ages = sorted(row["age_months"] for row in weighted)
    spread = upper_ppsm - lower_ppsm
    average_similarity = sum(row["similarity"] for row in weighted) / len(weighted)
    median_age = _median(ages)

    relaxed_rules = list(cast(list[str], filter_status["relaxation_steps"]))
    confidence, confidence_reasons = _confidence(
        effective_count,
        average_similarity,
        median_age,
        selected_level,
        spread / central_ppsm if central_ppsm else 1.0,
        missing_warnings,
    )
    # Use the canonical similarity first, then recency, then a stable ID. The
    # final key keeps the contextual evidence deterministic across requests.
    weighted.sort(key=lambda row: (-row["similarity"], row["age_months"], str(row["transaction_id"])))
    strongest = weighted[: config.strongest_count]
    return ComparableSelection(
        all_rows=weighted,
        rows=strongest,
        total_candidate_count=len(historical),
        eligible_comparable_count=len(weighted),
        effective_weighted_count=round(effective_count, 2),
        average_similarity=round(average_similarity, 4),
        median_transaction_age_months=round(median_age, 1),
        comparable_price_spread=round(spread * listing.floor_area_sqm, 0),
        comparable_estimate=round(central_ppsm * listing.floor_area_sqm, 0),
        comparable_price_per_sqm=round(central_ppsm, 2),
        comparable_lower_bound=round(max(0.0, lower_ppsm * listing.floor_area_sqm), 0),
        comparable_upper_bound=round(max(0.0, upper_ppsm * listing.floor_area_sqm), 0),
        relaxation_level=selected_level,
        relaxed_rules=relaxed_rules,
        missing_feature_warnings=missing_warnings,
        confidence=confidence,
        confidence_reasons=confidence_reasons,
        filter_status=filter_status,
        filter_messages=filter_messages,
        warning_details=warning_details,
        comparable_count_by_stage=comparable_count_by_stage,
    )


def _similarity_components(
    record: TransactionRecord,
    listing: ConfirmedListing,
    target_town: str | None,
    target_block: str | None,
    target_street: str | None,
    target_lease: int,
    decay_months: float,
    valuation_date: date,
) -> dict[str, float]:
    record_key = canonical_hdb_parts(record.block, record.street)
    if target_block and target_street and record_key == (target_block, target_street):
        location = 1.0
    elif target_street and record_key and record_key[1] == target_street:
        location = 0.9
    elif target_town and _normalise(record.town) == target_town:
        location = 0.75
    else:
        location = 0.45
    components = {
        "recency": math.exp(-max(0.0, _age_months(record.transaction_month, valuation_date)) / decay_months),
        "area": max(
            0.0,
            min(
                1.0,
                math.exp(
                    -abs(record.floor_area_sqm - listing.floor_area_sqm)
                    / max(1.0, listing.floor_area_sqm * 0.10)
                ),
            ),
        ),
        "lease": max(
            0.0,
            min(1.0, math.exp(-abs((_record_lease_months(record) or 0) - target_lease) / 96.0)),
        ),
        "location": location,
    }
    if listing.flat_model and record.flat_model:
        components["flat_model"] = 1.0 if _normalise(record.flat_model) == _normalise(listing.flat_model) else 0.65
    if listing.storey_range and record.storey_range:
        components["storey"] = _storey_similarity(record.storey_range, listing.storey_range)
    return components


def _storey_similarity(record_storey: str, listing_storey: str) -> float:
    record_midpoint = _storey_midpoint(record_storey)
    listing_midpoint = _storey_midpoint(listing_storey)
    if not record_midpoint or not listing_midpoint:
        return 1.0
    return max(0.55, min(1.0, math.exp(-abs(record_midpoint - listing_midpoint) / 8.0)))


def _storey_midpoint(value: str) -> float:
    numbers = [int(number) for number in re.findall(r"\d+", value)]
    return sum(numbers) / len(numbers) if numbers else 0.0


def _record_dict(
    record: TransactionRecord,
    *,
    age_months: float,
    weight: float,
    similarity: float,
    components: dict[str, float],
) -> dict:
    return {
        "transaction_id": record.transaction_id,
        "address": f"{record.block} {record.street}".strip(),
        "transaction_date": record.transaction_month,
        "month": record.transaction_month,
        "block": record.block,
        "street": record.street,
        "town": record.town,
        "flat_type": record.flat_type,
        "flat_model": record.flat_model,
        "floor_area_sqm": record.floor_area_sqm,
        "storey_range": record.storey_range,
        "remaining_lease": record.remaining_lease,
        "remaining_lease_months": _record_lease_months(record),
        "resale_price": record.resale_price,
        "price_per_sqm": record.price_per_sqm,
        "distance_km": None,
        "age_months": round(age_months, 1),
        "similarity": round(similarity, 5),
        "similarity_components": {key: round(value, 5) for key, value in components.items()},
        "weight": round(weight, 6),
    }


def _deduplicate_records(records: Iterable[TransactionRecord]) -> list[TransactionRecord]:
    """Remove repeated canonical transactions before eligibility and weighting."""
    unique: list[TransactionRecord] = []
    seen: set[str] = set()
    for record in records:
        transaction_id = str(record.transaction_id).strip()
        key = f"id:{transaction_id}" if transaction_id else "row:" + "|".join(
            [
                record.transaction_month,
                record.block,
                record.street,
                str(record.resale_price),
                str(record.floor_area_sqm),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _relaxed_rules(level: int, town: str | None, block: str | None) -> list[str]:
    result: list[str] = []
    if level >= 1:
        result.append("Remaining-lease tolerance widened to approximately ±12 years.")
    if level >= 2:
        result.append("Floor-area tolerance widened to approximately ±20%.")
    if level >= 3:
        result.append("Floor-area and remaining-lease tolerances widened further within the town.")
    if level >= 4 and town:
        result.append("Town restriction relaxed to a broad same-flat-type regional pool.")
    if level >= 5:
        result.append("Broad regional fallback used; confidence is capped at low.")
    return result


def _filter_status(
    listing: ConfirmedListing,
    target_town: str | None,
    target_flat_type: str | None,
    selected: list[TransactionRecord],
    selected_level: int,
) -> dict[str, object]:
    has_model_evidence = bool(listing.flat_model and any(record.flat_model for record in selected))
    has_storey_evidence = bool(listing.storey_range and any(record.storey_range for record in selected))
    return {
        "town": {
            "value": target_town,
            "status": (
                "relaxed"
                if target_town and selected_level >= 4
                else "applied"
                if target_town
                else "omitted_missing"
            ),
        },
        "flat_type": {
            "value": target_flat_type,
            "status": "applied" if target_flat_type else "omitted_missing",
        },
        "flat_model": {
            "value": listing.flat_model,
            "status": "applied" if has_model_evidence else "omitted_missing",
        },
        "area_band": {"status": "relaxed" if selected_level >= 2 else "applied"},
        "lease_band": {"status": "relaxed" if selected_level >= 1 else "applied"},
        "storey_range": {
            "value": listing.storey_range,
            "status": "applied" if has_storey_evidence else "omitted_missing",
        },
        "relaxation_steps": _relaxed_rules(selected_level, target_town, None),
    }


def _filter_messages(filter_status: dict[str, object], town_source: str | None) -> list[str]:
    messages: list[str] = []
    town = filter_status["town"]
    flat_type = filter_status["flat_type"]
    flat_model = filter_status["flat_model"]
    storey = filter_status["storey_range"]
    if isinstance(town, dict):
        if town["status"] == "applied":
            source_text = (
                "from the confirmed block address"
                if town_source == "historical_transaction_match"
                else "from address enrichment"
            )
            messages.append(f"Town was derived as {town['value']} {source_text}.")
        elif town["status"] == "relaxed":
            messages.append(f"Town {town['value']} was initially matched but the town filter was relaxed.")
        else:
            messages.append("Town could not be determined, so town matching was omitted.")
    if isinstance(flat_type, dict) and flat_type["status"] == "applied":
        messages.append(f"Comparables were matched using the same flat type ({flat_type['value']}).")
    if isinstance(filter_status["area_band"], dict):
        messages.append(
            "Similar floor-area bands were used."
            if filter_status["area_band"]["status"] == "applied"
            else "Floor-area tolerance was widened during comparable selection."
        )
    if isinstance(filter_status["lease_band"], dict):
        messages.append(
            "Similar remaining-lease bands were used."
            if filter_status["lease_band"]["status"] == "applied"
            else "Remaining-lease tolerance was widened during comparable selection."
        )
    if isinstance(flat_model, dict) and flat_model["status"] == "omitted_missing":
        base = flat_type["value"] if isinstance(flat_type, dict) else "the confirmed flat type"
        messages.append(
            f"Flat type was matched as {base}. The exact HDB flat model could not be confirmed, "
            "so flat-model similarity was omitted."
        )
    if isinstance(storey, dict) and storey["status"] == "omitted_missing":
        messages.append("Storey was not provided, so storey similarity was not applied.")
    steps = cast(list[str], filter_status["relaxation_steps"])
    if not steps:
        messages.append("No filter relaxation was required.")
    else:
        messages.extend(steps)
    return messages


def _warning_details(filter_status: dict[str, object], messages: list[str]) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    town = filter_status["town"]
    model = filter_status["flat_model"]
    storey = filter_status["storey_range"]
    if isinstance(town, dict) and town["status"] == "omitted_missing":
        town_message = next(message for message in messages if message.startswith("Town could not be determined"))
        details.append({"code": "TOWN_UNAVAILABLE", "severity": "warning", "message": town_message})
    if isinstance(model, dict) and model["status"] == "omitted_missing":
        model_message = next(message for message in messages if "flat model" in message)
        details.append({"code": "FLAT_MODEL_UNAVAILABLE", "severity": "info", "message": model_message})
    if isinstance(storey, dict) and storey["status"] == "omitted_missing":
        storey_message = next(message for message in messages if "Storey was not provided" in message)
        details.append({"code": "STOREY_NOT_PROVIDED", "severity": "info", "message": storey_message})
    for step in cast(list[str], filter_status["relaxation_steps"]):
        details.append({"code": "FILTER_RELAXED", "severity": "warning", "message": step})
    return details


def derive_town_match_evidence(records: Iterable[TransactionRecord], address: str) -> dict[str, object]:
    """Return auditable town-derivation evidence for one exact address key."""
    address_key = canonical_hdb_address_key(address)
    if address_key is None:
        return {
            "raw_listing_address": address,
            "canonical_address_key": None,
            "matching_transaction_count": 0,
            "matched_towns": [],
            "town": None,
            "source": None,
            "reason": "address_key_unavailable",
        }

    matching = [
        record
        for record in records
        if canonical_hdb_parts(record.block, record.street) == address_key
    ]
    towns = sorted({_normalise(record.town) for record in matching if record.town})
    if len(towns) == 1:
        town = towns[0]
        source = "historical_transaction_match"
        reason = "unique_canonical_address_match"
    else:
        town = None
        source = None
        reason = "no_matching_transactions" if not towns else "ambiguous_town_for_canonical_address"

    evidence = {
        "raw_listing_address": address,
        "canonical_address_key": {"block": address_key[0], "street": address_key[1]},
        "matching_transaction_count": len(matching),
        "matched_towns": towns,
        "matched_block_street_values": sorted({f"{record.block} {record.street}" for record in matching}),
        "town": town,
        "source": source,
        "reason": reason,
    }
    logger.debug("HDB town derivation", extra={"town_derivation": evidence})
    if reason == "ambiguous_town_for_canonical_address":
        logger.warning("Ambiguous HDB town derivation", extra={"town_derivation": evidence})
    return evidence


def derive_town_from_transactions(records: Iterable[TransactionRecord], address: str) -> tuple[str | None, str | None]:
    evidence = derive_town_match_evidence(records, address)
    return evidence["town"], evidence["source"]


def derive_lease_commencement_from_transactions(
    records: Iterable[TransactionRecord], address: str
) -> tuple[int | None, str | None]:
    """Find an unambiguous lease commencement year for an exact HDB block/street."""
    address_key = canonical_hdb_address_key(address)
    if address_key is None:
        return None, None
    commencement_years = {
        record.lease_commencement
        for record in records
        if canonical_hdb_parts(record.block, record.street) == address_key
        and record.lease_commencement > 0
    }
    if len(commencement_years) == 1:
        return next(iter(commencement_years)), "historical_transactions"
    return None, None


def infer_flat_model_from_transactions(
    records: Iterable[TransactionRecord], listing: ConfirmedListing
) -> tuple[str | None, str | None]:
    address_key = canonical_hdb_address_key(listing.address)
    target_type = _flat_type_key(listing.flat_type)
    if address_key is None or not target_type:
        return None, None
    models = [
        record.flat_model
        for record in records
        if canonical_hdb_parts(record.block, record.street) == address_key
        and _flat_type_key(record.flat_type) == target_type
        and record.flat_model
    ]
    # Historical inference is deliberately a conservative fallback. Explicit
    # recognised listing subtype evidence is resolved before this function.
    if len(models) < 5:
        return None, None
    counts = {model: models.count(model) for model in set(models)}
    model, count = max(counts.items(), key=lambda item: item[1])
    if count / len(models) >= 0.9:
        return model, "historical_transactions"
    return None, None


def _confidence(
    effective_count: float,
    average_similarity: float,
    median_age: float,
    relaxation_level: int,
    spread_ratio: float,
    missing_warnings: list[str],
) -> tuple[str, list[str]]:
    reasons = [f"{effective_count:.1f} effective weighted comparables", f"Average similarity {average_similarity:.2f}"]
    if median_age <= 8:
        reasons.append(f"Median comparable age {median_age:.1f} months")
    if relaxation_level:
        reasons.append(f"Search was relaxed to level {relaxation_level}")
    if spread_ratio > 0.35:
        reasons.append("Comparable price spread is wide")
    if missing_warnings:
        reasons.append("Some optional listing features were unavailable")
    if effective_count >= 8 and average_similarity >= 0.35 and relaxation_level <= 1 and spread_ratio <= 0.35:
        return "HIGH", reasons
    if effective_count >= 3 and average_similarity >= 0.18 and relaxation_level <= 3:
        return "MEDIUM", reasons
    return "LOW", reasons


def _weighted_quantile(pairs: list[tuple[float, float]], quantile: float) -> float:
    ordered = sorted(pairs, key=lambda pair: pair[0])
    total = sum(weight for _, weight in ordered)
    threshold = total * max(0.0, min(1.0, quantile))
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def _age_months(transaction_month: str, valuation_date: date) -> float:
    year, month = (int(part) for part in transaction_month.split("-"))
    return max(0.0, (valuation_date.year - year) * 12 + valuation_date.month - month)


def _shift_month(year: int, month: int, delta: int) -> str:
    index = year * 12 + month - 1 + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _normalise(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value).upper().strip()) or None


def _flat_type_key(value: str | None) -> str | None:
    """Match equivalent listing labels to HDB transaction flat-type labels."""
    normalised = _normalise(value)
    if not normalised:
        return None
    without_suffix = re.sub(r"\s+HDB$", "", normalised)
    return re.sub(r"\s*\([^)]*\)$", "", without_suffix).strip() or None


def _median(values: list[float]) -> float:
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
