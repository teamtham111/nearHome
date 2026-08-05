"""Absolute buyer-fit scoring with separate shortlist ranking metadata."""

from __future__ import annotations

import math
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.domain.enums import DataStatus, PriorityType
from app.domain.models import (
    BuyerProfile,
    ConfirmedListing,
    JourneyEstimate,
    MetricResult,
    PreferenceScore,
    Priority,
)

TIE_THRESHOLD = 3.0  # absolute overall-fit points


class PreferenceScoringEngine:
    SCORING_VERSION = settings.scoring_version

    @classmethod
    def score(
        cls,
        listings: list[ConfirmedListing],
        eligible_ids: list[UUID],
        buyer_profile: BuyerProfile,
        immediate_metrics: list[MetricResult],
        journey_estimates: list[JourneyEstimate] | None = None,
        enriched_fields_by_listing: dict[UUID, dict[str, Any]] | None = None,
    ) -> list[PreferenceScore]:
        if not buyer_profile.priorities:
            return []

        eligible = [l for l in listings if l.listing_id in eligible_ids]
        if not eligible:
            return []

        journey_estimates = journey_estimates or []
        enriched_fields_by_listing = enriched_fields_by_listing or {}
        raw_by_listing: dict[UUID, dict[str, float | None]] = {l.listing_id: {} for l in eligible}
        absolute_by_listing: dict[UUID, dict[str, float]] = {l.listing_id: {} for l in eligible}

        for priority in buyer_profile.priorities:
            pid = priority.identifier
            values = cls._collect_raw_values(
                priority,
                eligible,
                immediate_metrics,
                journey_estimates,
                enriched_fields_by_listing,
            )
            for lid, val in values.items():
                raw_by_listing[lid][pid] = val

            priority_scores = cls._absolute_scores(priority, values, buyer_profile)
            for lid, score in priority_scores.items():
                absolute_by_listing[lid][pid] = score

        scores: list[PreferenceScore] = []
        totals: list[float] = []
        for listing in eligible:
            lid = listing.listing_id
            listing_scores = absolute_by_listing[lid]
            weight_total = sum(
                p.weight for p in buyer_profile.priorities if p.identifier in listing_scores
            )
            weights = {
                p.identifier: p.weight / weight_total
                for p in buyer_profile.priorities
                if p.identifier in listing_scores and weight_total
            }
            sub_scores = {pid: score for pid, score in listing_scores.items() if pid in weights}
            total = sum(score * weights[pid] for pid, score in sub_scores.items()) if sub_scores else 0.0
            totals.append(total)
            scores.append(
                PreferenceScore(
                    listing_id=lid,
                    total_score=round(total, 2),
                    sub_scores={k: round(v, 2) for k, v in sub_scores.items()},
                    weights=weights,
                    raw_values=raw_by_listing[lid],
                    coverage=cls._coverage(listing_scores, buyer_profile.priorities),
                    is_tie_candidate=False,
                    trade_off_flags=[],
                    scoring_version=cls.SCORING_VERSION,
                    overall_fit_score=round(total, 2) if sub_scores else None,
                )
            )

        if len(totals) >= 2:
            max_t = max(totals)
            min_t = min(totals)
            tie = max_t - min_t <= TIE_THRESHOLD and max_t > 0
            for ps in scores:
                ps.is_tie_candidate = tie

        rank = 0
        previous_score: float | None = None
        ranked_scores = sorted(
            scores,
            key=lambda item: item.overall_fit_score if item.overall_fit_score is not None else -1,
            reverse=True,
        )
        for index, ps in enumerate(ranked_scores, start=1):
            score = ps.overall_fit_score
            if previous_score is None or score is None or abs(score - previous_score) > 0.01:
                rank = index
            ps.rank = rank
            previous_score = score

        return scores

    @classmethod
    def _collect_raw_values(
        cls,
        priority: Priority,
        listings: list[ConfirmedListing],
        metrics: list[MetricResult],
        journeys: list[JourneyEstimate],
        enriched_fields_by_listing: dict[UUID, dict[str, Any]],
    ) -> dict[UUID, float | None]:
        priority_type = priority.priority_type
        result: dict[UUID, float | None] = {}

        for listing in listings:
            lid = listing.listing_id
            if priority_type == PriorityType.AFFORDABILITY:
                result[lid] = listing.asking_price
            elif priority_type == PriorityType.SPACE:
                result[lid] = listing.floor_area_sqm
            elif priority_type == PriorityType.LEASE:
                if listing.remaining_lease_months is not None:
                    result[lid] = float(listing.remaining_lease_months)
                elif listing.remaining_lease_years is not None:
                    result[lid] = float(listing.remaining_lease_years) * 12
                else:
                    metric = next(
                        (
                            m
                            for m in metrics
                            if m.listing_id == lid
                            and m.metric_name in {"remaining_lease_months", "remaining_lease_years"}
                        ),
                        None,
                    )
                    if metric is None or not isinstance(metric.raw_value, (int, float)):
                        result[lid] = None
                    elif metric.metric_name == "remaining_lease_years":
                        result[lid] = float(metric.raw_value) * 12
                    else:
                        result[lid] = float(metric.raw_value)
            elif priority_type == PriorityType.IMPORTANT_LOCATION_JOURNEY:
                loc_journeys = [
                    j
                    for j in journeys
                    if j.listing_id == lid
                    and j.important_location_id == priority.important_location_id
                    and j.status == DataStatus.AVAILABLE
                    and j.duration_seconds is not None
                ]
                durations = [j.duration_seconds for j in loc_journeys if j.duration_seconds is not None]
                result[lid] = float(min(durations)) if durations else None
            elif priority_type in (PriorityType.PUBLIC_TRANSPORT, PriorityType.DRIVING):
                field_name = (
                    "public_transport"
                    if priority_type == PriorityType.PUBLIC_TRANSPORT
                    else "driving_access"
                )
                field = enriched_fields_by_listing.get(lid, {}).get(field_name) or {}
                # Both Public Transport and Driving store the recommendation-eligible
                # score under "overall_score" — None whenever assessed data coverage
                # falls below the model's minimum threshold (see ModelRollup).
                value = field.get("overall_score")
                result[lid] = (
                    float(value)
                    if isinstance(value, (int, float)) and math.isfinite(value)
                    else None
                )
            elif priority_type == PriorityType.FAIR_PRICE:
                field = enriched_fields_by_listing.get(lid, {}).get("fair_price") or {}
                estimate = field.get("final_estimate", field.get("central_estimate"))
                asking_price = listing.asking_price
                status = field.get("status")
                if (
                    isinstance(estimate, (int, float))
                    and math.isfinite(estimate)
                    and estimate > 0
                    and status == DataStatus.AVAILABLE.value
                ):
                    gap = (float(estimate) - asking_price) / float(estimate)
                    confidence = str(field.get("confidence", "LOW")).upper()
                    multiplier = {"HIGH": 1.0, "MEDIUM": 0.8, "LOW": 0.5}.get(confidence, 0.25)
                    result[lid] = gap * multiplier
                else:
                    result[lid] = None
            elif priority_type == PriorityType.SCHOOLS:
                field = enriched_fields_by_listing.get(lid, {}).get("schools") or {}
                value = field.get("score")
                result[lid] = (
                    float(value)
                    if isinstance(value, (int, float)) and math.isfinite(value)
                    else None
                )
            else:
                result[lid] = None
        return result

    @classmethod
    def _absolute_scores(
        cls,
        priority: Priority,
        values: dict[UUID, float | None],
        buyer_profile: BuyerProfile,
    ) -> dict[UUID, float]:
        available = {
            key: value
            for key, value in values.items()
            if value is not None and math.isfinite(value)
        }
        if not available:
            return {}
        return {
            key: round(cls._absolute_score(priority.priority_type, value, buyer_profile.max_budget), 2)
            for key, value in available.items()
        }

    @staticmethod
    def _absolute_score(priority_type: PriorityType, value: float, max_budget: float) -> float:
        """Return a stable 0–100 score that does not depend on peer listings."""
        if priority_type == PriorityType.AFFORDABILITY:
            ratio = value / max_budget if max_budget > 0 else math.inf
            score = 100 - max(0.0, ratio - 0.75) * 200
        elif priority_type == PriorityType.SPACE:
            score = (value - 60) / 60 * 100
        elif priority_type == PriorityType.LEASE:
            score = (value / 12 - 40) / 40 * 100
        elif priority_type == PriorityType.IMPORTANT_LOCATION_JOURNEY:
            score = 100 - (value / 60 - 15) / 60 * 100
        elif priority_type == PriorityType.FAIR_PRICE:
            score = 50 + value * 500
        else:
            score = value
        return max(0.0, min(100.0, score))

    @staticmethod
    def _coverage(sub_scores: dict[str, float], priorities: list[Priority]) -> float:
        if not priorities:
            return 0.0
        covered = sum(1 for priority in priorities if priority.identifier in sub_scores)
        return round(covered / len(priorities), 2)
