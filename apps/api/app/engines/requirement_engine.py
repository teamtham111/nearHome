"""Supported hard-requirement evaluation, including separate journey limits."""

from __future__ import annotations

from uuid import UUID

from app.core.config import settings
from app.domain.enums import (
    REJECTED_REQUIREMENT_METRICS,
    DataStatus,
    JourneyMode,
    ListingGroup,
    RequirementMetric,
    RequirementOperator,
    RequirementStatus,
)
from app.domain.models import (
    BuyerProfile,
    ConfirmedListing,
    HardRequirement,
    MetricResult,
    RequirementResult,
)


class RequirementRegistryError(ValueError):
    pass


class RequirementEngine:
    RULE_VERSION = settings.requirement_rule_version

    @classmethod
    def validate_requirement(cls, requirement: HardRequirement) -> None:
        metric_value = (
            requirement.metric.value if isinstance(requirement.metric, RequirementMetric) else str(requirement.metric)
        )
        if metric_value in REJECTED_REQUIREMENT_METRICS:
            raise RequirementRegistryError(
                f"Requirement metric '{metric_value}' is not supported. Journey duration cannot be a hard requirement."
            )
        if requirement.metric == RequirementMetric.MAX_DRIVING_JOURNEY_MINUTES:
            if requirement.important_location_id is None:
                raise RequirementRegistryError("A maximum driving journey requirement requires important_location_id.")
            if requirement.operator != RequirementOperator.LTE:
                raise RequirementRegistryError("Maximum driving journey requirements must use the LTE operator.")

    @classmethod
    def evaluate_all(
        cls,
        listings: list[ConfirmedListing],
        buyer_profile: BuyerProfile,
        immediate_metrics: list[MetricResult],
        journey_estimates: list | None = None,
    ) -> list[RequirementResult]:
        results: list[RequirementResult] = []
        for req in buyer_profile.hard_requirements:
            cls.validate_requirement(req)
            for listing in listings:
                results.append(cls._evaluate_one(listing, req, immediate_metrics, journey_estimates or []))
        return results

    @classmethod
    def _evaluate_one(
        cls,
        listing: ConfirmedListing,
        requirement: HardRequirement,
        immediate_metrics: list[MetricResult],
        journey_estimates: list,
    ) -> RequirementResult:
        actual, source_metric, status = cls._get_actual_value(
            listing, requirement, immediate_metrics, journey_estimates
        )

        if actual is None or status != DataStatus.AVAILABLE:
            return RequirementResult(
                listing_id=listing.listing_id,
                requirement=requirement,
                status=RequirementStatus.CANNOT_DETERMINE,
                actual_value=actual,
                threshold=requirement.threshold,
                difference_from_threshold=None,
                source_metric=source_metric,
                explanation=f"Cannot evaluate {requirement.metric.value}: required evidence unavailable",
                rule_version=cls.RULE_VERSION,
            )

        passed, diff, explanation = cls._compare(
            requirement.operator, actual, requirement.threshold, requirement.metric
        )
        return RequirementResult(
            listing_id=listing.listing_id,
            requirement=requirement,
            status=RequirementStatus.PASS if passed else RequirementStatus.FAIL,
            actual_value=actual,
            threshold=requirement.threshold,
            difference_from_threshold=diff,
            source_metric=source_metric,
            explanation=explanation,
            rule_version=cls.RULE_VERSION,
        )

    @staticmethod
    def _get_actual_value(
        listing: ConfirmedListing,
        requirement: HardRequirement,
        immediate_metrics: list[MetricResult],
        journey_estimates: list,
    ) -> tuple[float | str | None, str, DataStatus]:
        metric = requirement.metric
        if metric == RequirementMetric.FLOOR_AREA_SQM:
            return listing.floor_area_sqm, "floor_area_sqm", DataStatus.AVAILABLE
        if metric == RequirementMetric.FLAT_TYPE:
            return listing.flat_type, "flat_type", DataStatus.AVAILABLE
        if metric == RequirementMetric.REMAINING_LEASE_YEARS:
            if listing.remaining_lease_months is not None:
                return listing.remaining_lease_months, "remaining_lease_months", DataStatus.AVAILABLE
            if listing.remaining_lease_years is not None:
                return round(listing.remaining_lease_years * 12), "remaining_lease_months", DataStatus.AVAILABLE
            for m in immediate_metrics:
                if m.listing_id == listing.listing_id and m.metric_name in {
                    "remaining_lease_months",
                    "remaining_lease_years",
                }:
                    if m.status == DataStatus.AVAILABLE and m.raw_value is not None:
                        value = float(m.raw_value)
                        canonical = value if m.metric_name == "remaining_lease_months" else value * 12
                        return canonical, "remaining_lease_months", m.status
                    return None, "remaining_lease_months", m.status
            return None, "remaining_lease_months", DataStatus.NOT_PROVIDED_BY_USER
        if metric == RequirementMetric.MAX_DRIVING_JOURNEY_MINUTES:
            matching = [
                journey
                for journey in journey_estimates
                if journey.listing_id == listing.listing_id
                and journey.important_location_id == requirement.important_location_id
                and journey.mode == JourneyMode.DRIVING
            ]
            available = [
                journey.duration_seconds
                for journey in matching
                if journey.status == DataStatus.AVAILABLE and journey.duration_seconds is not None
            ]
            if available:
                return min(available) / 60, "regular_destination_journey_minutes", DataStatus.AVAILABLE
            status = DataStatus.TEMPORARILY_UNAVAILABLE if matching else DataStatus.NOT_PROVIDED_BY_USER
            return None, "regular_destination_journey_minutes", status
        return None, metric.value, DataStatus.NOT_APPLICABLE

    @staticmethod
    def _compare(
        operator: RequirementOperator,
        actual: float | str,
        threshold: float | str,
        metric: RequirementMetric,
    ) -> tuple[bool, float | None, str]:
        if metric == RequirementMetric.FLAT_TYPE:
            passed = str(actual).upper() == str(threshold).upper()
            return passed, None, f"Flat type is {actual}; required {threshold}"

        actual_f = float(actual)
        threshold_f = float(threshold)
        if metric == RequirementMetric.REMAINING_LEASE_YEARS:
            threshold_f *= 12
        if operator == RequirementOperator.LTE:
            passed = actual_f <= threshold_f
            diff = actual_f - threshold_f
            return passed, diff, f"Value {actual_f} {'≤' if passed else '>'} threshold {threshold_f}"
        if operator == RequirementOperator.GTE:
            passed = actual_f >= threshold_f
            diff = actual_f - threshold_f
            return passed, diff, f"Value {actual_f} {'≥' if passed else '<'} threshold {threshold_f}"
        passed = actual_f == threshold_f
        return passed, actual_f - threshold_f, f"Value {actual_f} {'==' if passed else '!='} {threshold_f}"

    @classmethod
    def classify_listings(
        cls,
        listing_ids: list[UUID],
        requirement_results: list[RequirementResult],
    ) -> dict[UUID, ListingGroup]:
        groups: dict[UUID, ListingGroup] = {}
        for lid in listing_ids:
            listing_reqs = [r for r in requirement_results if r.listing_id == lid]
            if not listing_reqs:
                groups[lid] = ListingGroup.PASSES_ALL
                continue
            fails = [r for r in listing_reqs if r.status == RequirementStatus.FAIL]
            unknowns = [r for r in listing_reqs if r.status == RequirementStatus.CANNOT_DETERMINE]
            if fails:
                groups[lid] = ListingGroup.FAILS_ONE if len(fails) == 1 else ListingGroup.FAILS_MULTIPLE
            elif unknowns:
                groups[lid] = ListingGroup.CANNOT_DETERMINE
            else:
                groups[lid] = ListingGroup.PASSES_ALL
        return groups

    @classmethod
    def eligible_listing_ids(
        cls,
        listing_ids: list[UUID],
        groups: dict[UUID, ListingGroup],
    ) -> list[UUID]:
        """Return IDs in the best available requirement group."""
        if any(groups[lid] == ListingGroup.PASSES_ALL for lid in listing_ids):
            return [lid for lid in listing_ids if groups[lid] == ListingGroup.PASSES_ALL]
        if any(groups[lid] == ListingGroup.CANNOT_DETERMINE for lid in listing_ids):
            return [lid for lid in listing_ids if groups[lid] == ListingGroup.CANNOT_DETERMINE]
        return list(listing_ids)
