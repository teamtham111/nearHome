"""Immediate factual comparison — no external APIs required."""

from __future__ import annotations

from app.domain.enums import DataStatus, Provenance
from app.domain.models import BuyerProfile, ConfirmedListing, MetricResult


class ImmediateComparisonEngine:
    """Calculate asking-price, budget, space and lease metrics."""

    @staticmethod
    def budget_difference(
        maximum_budget: float | None,
        asking_price: float | None,
    ) -> float | None:
        """Return remaining budget: positive under budget, negative over budget."""
        if maximum_budget is None or asking_price is None:
            return None
        return round(maximum_budget - asking_price, 2)

    @staticmethod
    def compute(
        listings: list[ConfirmedListing],
        buyer_profile: BuyerProfile | None,
    ) -> list[MetricResult]:
        max_budget = buyer_profile.max_budget if buyer_profile else None
        results: list[MetricResult] = []

        for listing in listings:
            price_per_sqm = listing.asking_price / listing.floor_area_sqm

            results.extend(
                [
                    MetricResult(
                        listing_id=listing.listing_id,
                        metric_name="asking_price",
                        raw_value=listing.asking_price,
                        unit="SGD",
                        score=None,
                        status=DataStatus.AVAILABLE,
                        explanation="User-confirmed asking price",
                        formula="asking_price",
                        provenance=Provenance.USER_ENTERED,
                    ),
                    MetricResult(
                        listing_id=listing.listing_id,
                        metric_name="price_per_sqm",
                        raw_value=round(price_per_sqm, 2),
                        unit="SGD/sqm",
                        score=None,
                        status=DataStatus.AVAILABLE,
                        explanation="Asking price divided by floor area in sqm",
                        formula="asking_price / floor_area_sqm",
                        provenance=Provenance.CALCULATED,
                    ),
                    MetricResult(
                        listing_id=listing.listing_id,
                        metric_name="floor_area_sqm",
                        raw_value=listing.floor_area_sqm,
                        unit="sqm",
                        score=None,
                        status=DataStatus.AVAILABLE,
                        explanation="User-confirmed floor area",
                        provenance=Provenance.USER_ENTERED,
                    ),
                ]
            )

            if max_budget is not None:
                budget_diff = ImmediateComparisonEngine.budget_difference(
                    max_budget, listing.asking_price
                )
                results.extend(
                    [
                        MetricResult(
                            listing_id=listing.listing_id,
                            metric_name="budget_difference",
                            raw_value=round(budget_diff, 2),
                            unit="SGD",
                            score=None,
                            status=DataStatus.AVAILABLE,
                            explanation="Maximum purchase budget minus asking price",
                            formula="max_budget - asking_price",
                            provenance=Provenance.CALCULATED,
                        ),
                    ]
                )

            remaining_months = listing.remaining_lease_months
            if remaining_months is None and listing.remaining_lease_years is not None:
                remaining_months = round(listing.remaining_lease_years * 12)
            if remaining_months is not None:
                lease_status = listing.remaining_lease_status
            elif listing.lease_commencement_year is not None:
                lease_status = DataStatus.AVAILABLE
                remaining_months = None
            else:
                lease_status = DataStatus.NOT_PROVIDED_BY_USER
                remaining_value = None

            remaining_value = round(remaining_months / 12, 2) if remaining_months is not None else None

            results.append(
                MetricResult(
                    listing_id=listing.listing_id,
                    metric_name="remaining_lease_years",
                    raw_value=remaining_value,
                    unit="years",
                    score=None,
                    status=lease_status,
                    explanation="Estimated remaining lease from user or official data",
                    provenance=Provenance.CALCULATED,
                )
            )

        return results
