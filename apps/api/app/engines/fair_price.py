"""CatBoost fair-price valuation with transparent comparable evidence."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.adapters.base import TransactionRecord
from app.adapters.factory import get_transactions_adapter
from app.domain.enums import ConfidenceLevel, DataStatus
from app.domain.models import ConfirmedListing
from app.engines.fair_price_catboost import MODEL_VERSION as CATBOOST_MODEL_VERSION
from app.engines.fair_price_catboost import predict as predict_catboost
from app.engines.fair_price_comparables import (
    DEFAULT_COMPARABLE_CONFIG,
    ComparableConfig,
    ComparableSelection,
    select_comparables,
)

logger = logging.getLogger(__name__)


@dataclass
class FairPriceResult:
    central_estimate: float | None
    range_low: float | None
    range_high: float | None
    asking_difference_dollars: float | None
    asking_difference_pct: float | None
    confidence: ConfidenceLevel
    confidence_reasons: list[str]
    comparables: list[dict]
    method: str
    status: DataStatus
    value_gap_percentage: float | None = None
    all_comparables: list[dict] | None = None
    model_version: str = CATBOOST_MODEL_VERSION
    warnings: list[str] | None = None
    evidence: dict[str, Any] | None = None
    comparable_model_version: str = "weighted_comparables_v3"
    filter_status: dict[str, object] | None = None
    filter_messages: list[str] | None = None
    warning_details: list[dict[str, str]] | None = None
    comparable_count_by_stage: dict[str, int] | None = None


class FairPriceEngine:
    """Calculate fair price with CatBoost and retain comparable evidence."""

    MODEL_VERSION = CATBOOST_MODEL_VERSION

    @classmethod
    def estimate(
        cls,
        listing: ConfirmedListing,
        town: str | None,
        *,
        valuation_date: date | None = None,
        records: list[TransactionRecord] | None = None,
        comparable_config: ComparableConfig = DEFAULT_COMPARABLE_CONFIG,
        town_source: str | None = None,
    ) -> FairPriceResult:
        valuation_date = valuation_date or date.today()
        lease_months = listing.remaining_lease_months
        if lease_months is None and listing.remaining_lease_years is not None:
            lease_months = round(listing.remaining_lease_years * 12)
        if lease_months is None or lease_months <= 0 or listing.floor_area_sqm <= 0:
            return cls._insufficient("Remaining lease and floor area are required for valuation.")

        adapter = get_transactions_adapter()
        records = records if records is not None else adapter.all_records()
        selection = select_comparables(records, listing, town, valuation_date, comparable_config, town_source)
        model_warning: str | None = None
        try:
            prediction = predict_catboost(listing, town, valuation_date, records)
        except Exception as exc:  # noqa: BLE001 - explicit production fallback is recorded
            prediction = None
            model_warning = f"CatBoost prediction failed; weighted-comparable fallback used: {type(exc).__name__}."
            logger.exception("fair_price_catboost_failed")

        if prediction is not None:
            if selection is not None:
                warnings = list(selection.filter_messages)
                confidence = _confidence_level(selection)
                comparables = selection.rows
                all_comparables = selection.all_rows
                filter_status = selection.filter_status
                filter_messages = selection.filter_messages
                warning_details = selection.warning_details
                comparable_count_by_stage = selection.comparable_count_by_stage
            else:
                warnings = ["No defensible comparable evidence was available; CatBoost model estimate shown."]
                confidence = ConfidenceLevel.MEDIUM if prediction.calibration_rows >= 100 else ConfidenceLevel.LOW
                comparables = []
                all_comparables = []
                filter_status = None
                filter_messages = []
                warning_details = [
                    {
                        "code": "COMPARABLES_UNAVAILABLE",
                        "severity": "warning",
                        "message": warnings[0],
                    }
                ]
                comparable_count_by_stage = {}
            estimate = prediction.central_estimate
            difference = listing.asking_price - estimate
            reasons = [
                *([*selection.confidence_reasons] if selection is not None else []),
                "Method: CATBOOST",
                f"CatBoost trained on {prediction.training_rows:,} historical transactions.",
            ]
            return FairPriceResult(
                central_estimate=estimate,
                range_low=prediction.range_low,
                range_high=prediction.range_high,
                asking_difference_dollars=round(difference, 0),
                asking_difference_pct=round(difference / estimate * 100, 1) if estimate else None,
                value_gap_percentage=round((estimate - listing.asking_price) / estimate * 100, 1)
                if estimate
                else None,
                confidence=confidence,
                confidence_reasons=reasons,
                comparables=comparables,
                all_comparables=all_comparables,
                method="CATBOOST",
                status=DataStatus.AVAILABLE,
                model_version=cls.MODEL_VERSION,
                warnings=warnings,
                evidence=_evidence(
                    selection,
                    {
                        "model": "CATBOOST",
                        "model_version": prediction.model_version,
                        "training_rows": prediction.training_rows,
                        "calibration_rows": prediction.calibration_rows,
                        "calibration_source": prediction.calibration_source,
                    },
                ),
                comparable_model_version="weighted_comparables_v3",
                filter_status=filter_status,
                filter_messages=filter_messages,
                warning_details=warning_details,
                comparable_count_by_stage=comparable_count_by_stage,
            )

        if selection is None:
            return cls._insufficient(model_warning or "No defensible comparable evidence is available.")

        estimate = selection.comparable_estimate
        lower = selection.comparable_lower_bound
        upper = selection.comparable_upper_bound
        warnings = list(selection.filter_messages)
        reasons = [*selection.confidence_reasons, "Method: WEIGHTED_COMPARABLES_FALLBACK"]
        if model_warning:
            reasons.append(model_warning)
        confidence = _confidence_level(selection)
        difference = listing.asking_price - estimate
        return FairPriceResult(
            central_estimate=round(estimate, 0),
            range_low=round(lower, 0),
            range_high=round(upper, 0),
            asking_difference_dollars=round(difference, 0),
            asking_difference_pct=round(difference / estimate * 100, 1) if estimate else None,
            value_gap_percentage=round((estimate - listing.asking_price) / estimate * 100, 1) if estimate else None,
            confidence=confidence,
            confidence_reasons=reasons,
            comparables=selection.rows,
            all_comparables=selection.all_rows,
            method="WEIGHTED_COMPARABLES_FALLBACK",
            status=DataStatus.AVAILABLE,
            model_version="weighted_comparables_v3",
            warnings=[*warnings, *([model_warning] if model_warning else [])],
            evidence=_evidence(selection, {"model": "WEIGHTED_COMPARABLES_FALLBACK"}),
            comparable_model_version="weighted_comparables_v3",
            filter_status=selection.filter_status,
            filter_messages=selection.filter_messages,
            warning_details=selection.warning_details,
            comparable_count_by_stage=selection.comparable_count_by_stage,
        )

    @classmethod
    def _insufficient(cls, reason: str) -> FairPriceResult:
        return FairPriceResult(
            central_estimate=None,
            range_low=None,
            range_high=None,
            asking_difference_dollars=None,
            asking_difference_pct=None,
            value_gap_percentage=None,
            confidence=ConfidenceLevel.NONE,
            confidence_reasons=[reason],
            comparables=[],
            all_comparables=[],
            method="INSUFFICIENT_EVIDENCE",
            status=DataStatus.INSUFFICIENT_EVIDENCE,
            model_version=cls.MODEL_VERSION,
            warnings=[reason],
            evidence=_evidence(None),
            comparable_model_version=cls.MODEL_VERSION,
            filter_status=None,
            filter_messages=[],
            warning_details=[{"code": "FAIR_PRICE_UNAVAILABLE", "severity": "error", "message": reason}],
            comparable_count_by_stage={},
        )


def _confidence_level(selection: ComparableSelection) -> ConfidenceLevel:
    return {
        "HIGH": ConfidenceLevel.HIGH,
        "MEDIUM": ConfidenceLevel.MEDIUM,
        "LOW": ConfidenceLevel.LOW,
    }.get(selection.confidence, ConfidenceLevel.LOW)


def _evidence(selection: ComparableSelection | None, model: dict[str, Any] | None = None) -> dict[str, Any]:
    if selection is None:
        return {
            "total_candidate_count": 0,
            "eligible_comparable_count": 0,
            "effective_weighted_count": 0,
            "average_similarity": 0,
            "median_age_months": None,
            "relaxation_level": None,
            "relaxed_rules": [],
            "missing_feature_warnings": [],
            "comparable_price_spread": None,
            "filter_status": None,
            "filter_messages": [],
            "comparable_count_by_stage": {},
            "model": model or {"model": "CATBOOST", "model_version": FairPriceEngine.MODEL_VERSION},
        }
    return {
        "total_candidate_count": selection.total_candidate_count,
        "eligible_comparable_count": selection.eligible_comparable_count,
        "effective_weighted_count": selection.effective_weighted_count,
        "average_similarity": selection.average_similarity,
        "median_age_months": selection.median_transaction_age_months,
        "relaxation_level": selection.relaxation_level,
        "relaxed_rules": selection.relaxed_rules,
        "missing_feature_warnings": selection.missing_feature_warnings,
        "comparable_price_spread": selection.comparable_price_spread,
        "filter_status": selection.filter_status,
        "filter_messages": selection.filter_messages,
        "comparable_count_by_stage": selection.comparable_count_by_stage,
        "model": model or {"model": "CATBOOST", "model_version": FairPriceEngine.MODEL_VERSION},
    }
