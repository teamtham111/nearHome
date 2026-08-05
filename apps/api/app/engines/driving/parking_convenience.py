"""Deterministic Home Parking Convenience scoring.

The scorer keeps parking separate from road connectivity and uses official
HDB static records plus optional official live availability. A single live
reading is displayed but is deliberately excluded from the score until the
database contains enough historical observations.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from app.adapters.parking.hdb_availability import (
    AvailabilityProviderError,
    AvailabilityRecord,
    CarparkAvailabilityProvider,
)
from app.adapters.parking.hdb_carpark import CarparkCandidate, HdbCarpark, HdbCarparkStore, normalize_carpark_type
from app.adapters.routing.base import RoutingProvider, RoutingProviderError
from app.core.config import settings
from app.domain.enums import ComponentStatus, Provenance
from app.domain.transport_models import ComponentResult, not_assessed, provider_error
from app.engines.transport_config import DRIVING_CONFIG, DrivingConfig

PARKING_SCORE_VERSION = "parking-v2"


def _walk_score(minutes: float, config: DrivingConfig) -> float:
    thresholds = config.parking_walk_score_thresholds
    for cutoff, score in thresholds:
        if minutes <= cutoff:
            return score
    return config.parking_walk_floor_score


def _type_score(carpark: HdbCarpark) -> float:
    return {
        "BASEMENT": 100.0,
        "MULTI_STOREY": 82.0,
        "SURFACE_AND_MULTI_STOREY": 78.0,
        "SURFACE": 48.0,
        "OTHER": 55.0,
    }.get(normalize_carpark_type(carpark.carpark_type), 50.0)


def _restriction_score(carpark: HdbCarpark) -> float | None:
    values = [carpark.short_term_parking, carpark.night_parking, carpark.parking_system_type]
    if not any(values):
        return None
    score = 50.0
    short_term = (carpark.short_term_parking or "").upper()
    night = (carpark.night_parking or "").upper()
    system = (carpark.parking_system_type or "").upper()
    if short_term and short_term != "NO":
        score += 25.0
    if night == "YES":
        score += 15.0
    if "ELECTRONIC" in system:
        score += 10.0
    return min(100.0, score)


def _capacity_score(total_lots: int | None) -> float | None:
    if total_lots is None or total_lots <= 0:
        return None
    return round(min(100.0, 40.0 + min(total_lots, 500) / 500 * 60.0), 1)


def _historical_score(summary: dict[str, Any] | None, min_samples: int) -> float | None:
    if not summary or summary.get("sample_size", 0) < min_samples:
        return None
    median = summary.get("median_weekday_availability_pct")
    if median is None:
        median = summary.get("median_availability_pct")
    return round(max(0.0, min(100.0, float(median))), 1) if median is not None else None


def _candidate_from_legacy_nearest(latitude: float, longitude: float, config: DrivingConfig) -> list[CarparkCandidate]:
    match = HdbCarparkStore.nearest(latitude, longitude, config.carpark_prefilter_m)
    if not match:
        return []
    carpark, distance = match
    return [CarparkCandidate(carpark, distance, 100.0, "NEAREST_GEOGRAPHIC_CANDIDATE")]


def compute_parking_convenience(
    latitude: float,
    longitude: float,
    routing: RoutingProvider,
    config: DrivingConfig = DRIVING_CONFIG,
    availability_provider: CarparkAvailabilityProvider | None = None,
    history_lookup: Callable[[str, str | None], dict[str, Any]] | None = None,
    address: str | None = None,
) -> ComponentResult:
    weight = config.weight_parking_convenience

    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        return not_assessed("parking_convenience", weight, "Listing coordinates are missing or invalid.")

    if not HdbCarparkStore.is_usable():
        return not_assessed(
            "parking_convenience",
            weight,
            "Official HDB carpark static data is unavailable.",
            limitations=["Refresh data_pipeline/ingest_hdb_carparks.py with --live."],
        )

    candidates = HdbCarparkStore.nearby(
        latitude,
        longitude,
        config.carpark_prefilter_m,
        config.max_carparks_evaluated,
        address=address,
    )
    # Keep compatibility with the original provider seam and old callers that
    # replace `nearest` in tests or integrations.
    if not candidates:
        candidates = _candidate_from_legacy_nearest(latitude, longitude, config)
    if not candidates:
        return not_assessed(
            "parking_convenience",
            weight,
            f"No official HDB carpark was found within {config.carpark_prefilter_m:g} metres of this listing.",
        )

    availability_records: list[AvailabilityRecord] = []
    availability_error: str | None = None
    if availability_provider is not None:
        try:
            availability_records = availability_provider.fetch()
        except AvailabilityProviderError as exc:
            availability_error = str(exc)

    availability_by_cp: dict[str, list[AvailabilityRecord]] = {}
    for record in availability_records:
        availability_by_cp.setdefault(record.carpark_no, []).append(record)

    routed: list[dict[str, Any]] = []
    route_succeeded = False
    for rank, candidate in enumerate(candidates, 1):
        try:
            route = routing.get_walking_route(
                (latitude, longitude), (candidate.carpark.latitude, candidate.carpark.longitude)
            )
            route_succeeded = True
            walk_minutes = float(route.duration_minutes)
            if walk_minutes <= config.max_practical_carpark_walk_minutes:
                candidate_records = availability_by_cp.get(candidate.carpark.carpark_no, [])
                candidate_car_lot = next((record for record in candidate_records if record.lot_type == "C"), None)
                relevance_parts: list[float] = [
                    _walk_score(walk_minutes, config),
                    _type_score(candidate.carpark),
                    candidate.relevance_score,
                ]
                if candidate_car_lot is not None:
                    relevance_parts.append(100.0)
                    capacity = _capacity_score(candidate_car_lot.total_lots)
                    if capacity is not None:
                        relevance_parts.append(capacity)
                routed.append(
                    {
                        "candidate": candidate,
                        "rank": rank,
                        "walk_minutes": round(walk_minutes, 1),
                        "walk_distance_metres": route.distance_metres,
                        "relevance_score": round(sum(relevance_parts) / len(relevance_parts), 1),
                    }
                )
        except RoutingProviderError:
            continue

    if not routed:
        if route_succeeded:
            return not_assessed(
                "parking_convenience",
                weight,
                "Nearby official carparks were routable but all exceeded the "
                f"{config.max_practical_carpark_walk_minutes:g}-minute practical walking limit.",
            )
        return provider_error(
            "parking_convenience",
            weight,
            "Routing provider could not confirm walking access to any nearby HDB carpark candidate.",
        )

    routed.sort(key=lambda item: (item["walk_minutes"], -item["relevance_score"]))
    primary = routed[0]
    reasonable_250 = sum(item["candidate"].haversine_distance_m <= config.carpark_reasonable_250m for item in routed)
    reasonable_500 = sum(item["candidate"].haversine_distance_m <= config.carpark_prefilter_m for item in routed)
    primary_cp: HdbCarpark = primary["candidate"].carpark
    primary_records = availability_by_cp.get(primary_cp.carpark_no, [])
    car_lot = next((record for record in primary_records if record.lot_type == "C"), None)
    historical = history_lookup(primary_cp.carpark_no, "C") if history_lookup else None
    typical_score = _historical_score(historical, settings.hdb_carpark_history_min_samples)
    sub_scores: dict[str, tuple[float | None, float]] = {
        "walking": (_walk_score(primary["walk_minutes"], config) + min(10.0, max(0, reasonable_500 - 1) * 2.0), 0.35),
        "type_and_shelter": (_type_score(primary_cp), 0.20),
        "restrictions": (_restriction_score(primary_cp), 0.10),
        "capacity": (None, 0.15),
        "typical_availability": (None, 0.20),
    }
    capacity = _capacity_score(car_lot.total_lots if car_lot else None)
    sub_scores["capacity"] = (capacity, 0.15)
    sub_scores["typical_availability"] = (typical_score, 0.20)

    assessed_weight = sum(weight_value for score, weight_value in sub_scores.values() if score is not None)
    score = round(
        sum(score_value * weight_value for score_value, weight_value in sub_scores.values() if score_value is not None)
        / assessed_weight,
        1,
    )
    confidence = "high" if typical_score is not None and car_lot else "medium"

    evidence_candidates: list[dict[str, Any]] = []
    for output_rank, item in enumerate(routed, 1):
        candidate = item["candidate"]
        carpark = candidate.carpark
        records = availability_by_cp.get(carpark.carpark_no, [])
        candidate_lot = next((record for record in records if record.lot_type == "C"), None)
        evidence_candidates.append(
            {
                "rank": output_rank,
                "carpark_no": carpark.carpark_no,
                "address": carpark.address,
                "carpark_type": carpark.carpark_type,
                "source_carpark_type": carpark.source_carpark_type,
                "parking_system_type": carpark.parking_system_type,
                "short_term_parking": carpark.short_term_parking,
                "free_parking": carpark.free_parking,
                "night_parking": carpark.night_parking,
                "carpark_decks": carpark.carpark_decks,
                "gantry_height_m": carpark.gantry_height_m,
                "basement_indicator": carpark.basement_indicator,
                "haversine_distance_m": round(candidate.haversine_distance_m, 1),
                "walk_distance_metres": item["walk_distance_metres"],
                "walk_minutes": item["walk_minutes"],
                "relevance_score": item["relevance_score"],
                "match_type": candidate.match_type,
                "confidence": "calculated",
                "sheltered_status": (
                    "YES" if carpark.is_sheltered is True else "NO" if carpark.is_sheltered is False else "UNKNOWN"
                ),
                "availability": _availability_dict(
                    candidate_lot, historical if carpark.carpark_no == primary_cp.carpark_no else None
                ),
            }
        )

    value = {
        "score_version": PARKING_SCORE_VERSION,
        "primary_carpark": evidence_candidates[0],
        "alternative_carparks": evidence_candidates[1:],
        "candidates": evidence_candidates,
        "reasonable_carparks_within_250m": reasonable_250,
        "reasonable_carparks_within_500m": reasonable_500,
        "subscores": {
            key: {"score": score_value, "weight": weight_value, "included": score_value is not None}
            for key, (score_value, weight_value) in sub_scores.items()
        },
        "availability_status": (
            "TEMPORARILY_UNAVAILABLE"
            if availability_error
            else "LIVE"
            if car_lot and car_lot.status == "LIVE"
            else "NOT_COVERED"
            if not primary_records
            else primary_records[0].status
        ),
        "availability_error": availability_error,
        "typical_availability": (
            historical
            if historical and typical_score is not None
            else {"status": "INSUFFICIENT_HISTORY", "sample_size": (historical or {}).get("sample_size", 0)}
        ),
    }
    limitations = [
        "Primary carpark is selected from up to five official candidates using routed walk time and "
        "geographic relevance; it is not a claim of resident allocation.",
        "Carpark-outline spatial matching is not used because the outline dataset has no reliable "
        "carpark-number join in the current source.",
    ]
    if availability_error:
        limitations.append("Live availability is temporarily unavailable; missing data was not treated as zero.")
    elif not primary_records:
        limitations.append("This carpark is not covered by the official live availability feed.")
    elif typical_score is None:
        limitations.append(
            "Live availability is informational only until enough historical observations are collected."
        )

    return ComponentResult(
        name="parking_convenience",
        value=value,
        score=score,
        weight=weight,
        status=ComponentStatus.CALCULATED,
        explanation=(
            f"Home parking is rated {_parking_label(score)} because the likely {primary_cp.carpark_type or 'official'} "
            f"carpark ({primary_cp.carpark_no}) is approximately a {primary['walk_minutes']:.1f}-minute walk away "
            f"with {reasonable_500} practical candidate(s) within {config.carpark_prefilter_m:g} metres."
        ),
        strengths=[
            f"Primary carpark: {primary_cp.address} ({primary['walk_minutes']:.1f} min walk).",
            f"{reasonable_500} practical nearby carpark candidate(s), including {reasonable_250} within 250 metres.",
        ],
        limitations=limitations,
        evidence=evidence_candidates,
        source="HDB Carpark Information + HDB Carpark Availability (data.gov.sg) + Google Routes walking",
        provenance=Provenance.ROUTED_LIVE,
        confidence=confidence,
    )


def _availability_dict(record: AvailabilityRecord | None, historical: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        return {"status": "NOT_COVERED"}
    return {
        "status": record.status,
        "lot_type": record.lot_type,
        "total_lots": record.total_lots,
        "available_lots": record.available_lots,
        "availability_pct": record.availability_pct,
        "updated_at": record.observed_at.isoformat() if record.timestamp_valid else None,
        "timestamp_valid": record.timestamp_valid,
        "history": historical,
    }


def _parking_label(score: float) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Moderate"
    return "Weak"
