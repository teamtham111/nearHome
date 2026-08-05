"""Production CatBoost fair-price estimator.

The estimator deliberately shares the feature definitions and hyperparameters
used by the offline challenger benchmark. Training data is restricted to
transactions before the valuation month, so a historical valuation cannot see
future prices. The model is cached for the immutable transaction snapshot and
the prediction interval is calibrated on the latest historical holdout.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from app.adapters.base import TransactionRecord
from app.domain.models import ConfirmedListing
from app.engines.fair_price_comparables import _flat_type_key
from app.evaluation.data import CATEGORICAL_FEATURES, NUMERIC_FEATURES

logger = logging.getLogger(__name__)

MODEL_VERSION = "catboost_v1"
_ITERATIONS = 400
_SEED = 42
_CACHE: dict[str, _FittedCatBoost] = {}


@dataclass(frozen=True)
class CatBoostPrediction:
    central_estimate: float
    range_low: float
    range_high: float
    training_rows: int
    calibration_rows: int
    calibration_source: str
    model_version: str = MODEL_VERSION


@dataclass
class _FittedCatBoost:
    model: Any
    numeric_medians: pd.Series
    residual_low: float
    residual_high: float
    training_rows: int
    calibration_rows: int
    calibration_source: str
    supported_flat_types: frozenset[str]


def predict(
    listing: ConfirmedListing,
    town: str | None,
    valuation_date: date,
    records: list[TransactionRecord],
) -> CatBoostPrediction | None:
    """Predict a fair price from the transaction snapshot, or return ``None``.

    ``None`` means the runtime model cannot make a defensible prediction from
    the supplied data. The caller must then use its explicit evidence-based
    fallback or report insufficient evidence.
    """

    valuation_month = valuation_date.strftime("%Y-%m")
    history = _records_to_frame(records, before_month=valuation_month)
    target_flat_type = _flat_type_key(listing.flat_type)
    target_town = str(town).strip().upper() if town else None
    if (
        history.empty
        or target_flat_type is None
        or target_flat_type not in set(history["flat_type"])
        or (target_town is not None and target_town not in set(history["town"]))
    ):
        return None

    cache_key = _snapshot_key(history)
    fitted = _CACHE.get(cache_key)
    if fitted is None:
        fitted = _fit(history)
        _CACHE[cache_key] = fitted

    target = _listing_frame(listing, town, valuation_date)
    target = _prepare_frame(target, fitted.numeric_medians)
    predicted = float(
        np.asarray(fitted.model.predict(target[CATEGORICAL_FEATURES + NUMERIC_FEATURES]), dtype=float)[0]
    )
    if not np.isfinite(predicted) or predicted <= 0:
        return None
    return CatBoostPrediction(
        central_estimate=round(predicted, 0),
        range_low=round(max(0.0, predicted + fitted.residual_low), 0),
        range_high=round(max(predicted, predicted + fitted.residual_high), 0),
        training_rows=fitted.training_rows,
        calibration_rows=fitted.calibration_rows,
        calibration_source=fitted.calibration_source,
    )


def _fit(history: pd.DataFrame) -> _FittedCatBoost:
    x = history[NUMERIC_FEATURES + CATEGORICAL_FEATURES]
    y = history["resale_price"].to_numpy(dtype=float)
    numeric_medians = _numeric_medians(x)
    prepared = _prepare_frame(x, numeric_medians)
    catboost_frame = prepared[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
    cat_features = list(range(len(CATEGORICAL_FEATURES)))

    model = _new_model()
    model.fit(catboost_frame, y, cat_features=cat_features)

    residual_low, residual_high, calibration_rows, calibration_source = _calibrate(history)
    return _FittedCatBoost(
        model=model,
        numeric_medians=numeric_medians,
        residual_low=residual_low,
        residual_high=residual_high,
        training_rows=len(history),
        calibration_rows=calibration_rows,
        calibration_source=calibration_source,
        supported_flat_types=frozenset(history["flat_type"]),
    )


def _calibrate(history: pd.DataFrame) -> tuple[float, float, int, str]:
    """Build an asymmetric empirical interval from a temporal holdout."""

    months = sorted(history["transaction_month"].unique())
    if len(months) < 13:
        return _in_sample_residuals(history)

    calibration_months = set(months[-12:])
    train = history.loc[~history["transaction_month"].isin(calibration_months)].copy()
    calibration = history.loc[history["transaction_month"].isin(calibration_months)].copy()
    if len(train) < 10 or calibration.empty:
        return _in_sample_residuals(history)

    numeric_medians = _numeric_medians(train[NUMERIC_FEATURES])
    model = _new_model(iterations=250)
    model.fit(
        _prepare_frame(train[NUMERIC_FEATURES + CATEGORICAL_FEATURES], numeric_medians)[
            CATEGORICAL_FEATURES + NUMERIC_FEATURES
        ],
        train["resale_price"].to_numpy(dtype=float),
        cat_features=list(range(len(CATEGORICAL_FEATURES))),
    )
    predicted = np.asarray(
        model.predict(
            _prepare_frame(calibration[NUMERIC_FEATURES + CATEGORICAL_FEATURES], numeric_medians)[
                CATEGORICAL_FEATURES + NUMERIC_FEATURES
            ]
        ),
        dtype=float,
    )
    residuals = calibration["resale_price"].to_numpy(dtype=float) - predicted
    return (
        float(np.quantile(residuals, 0.10)),
        float(np.quantile(residuals, 0.90)),
        len(calibration),
        "temporal_holdout",
    )


def _in_sample_residuals(history: pd.DataFrame) -> tuple[float, float, int, str]:
    """Use a conservative residual interval for small snapshots only."""

    # This path is primarily for tests or a newly ingested small fixture. The
    # full HDB snapshot always has enough months for the temporal calibration.
    from catboost import CatBoostRegressor

    numeric_medians = _numeric_medians(history[NUMERIC_FEATURES])
    model = CatBoostRegressor(
        iterations=120,
        depth=8,
        learning_rate=0.05,
        loss_function="MAE",
        random_seed=_SEED,
        verbose=False,
        allow_writing_files=False,
        thread_count=4,
    )
    prepared = _prepare_frame(history[NUMERIC_FEATURES + CATEGORICAL_FEATURES], numeric_medians)
    model.fit(
        prepared[CATEGORICAL_FEATURES + NUMERIC_FEATURES],
        history["resale_price"].to_numpy(dtype=float),
        cat_features=list(range(len(CATEGORICAL_FEATURES))),
    )
    residuals = history["resale_price"].to_numpy(dtype=float) - np.asarray(
        model.predict(prepared[CATEGORICAL_FEATURES + NUMERIC_FEATURES]), dtype=float
    )
    return float(np.quantile(residuals, 0.10)), float(np.quantile(residuals, 0.90)), len(history), "in_sample"


def _new_model(iterations: int = _ITERATIONS) -> Any:
    from catboost import CatBoostRegressor

    return CatBoostRegressor(
        iterations=iterations,
        depth=8,
        learning_rate=0.05,
        loss_function="MAE",
        random_seed=_SEED,
        verbose=False,
        allow_writing_files=False,
        thread_count=4,
    )


def _records_to_frame(records: list[TransactionRecord], before_month: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.transaction_month >= before_month:
            continue
        try:
            transaction_date = datetime.strptime(record.transaction_month, "%Y-%m")
        except ValueError:
            continue
        if record.floor_area_sqm <= 0 or record.resale_price <= 0 or not record.town or not record.flat_type:
            continue
        if record.lease_commencement < 1900 or record.lease_commencement > transaction_date.year:
            continue
        month_index = transaction_date.year * 12 + transaction_date.month
        rows.append(
            {
                "transaction_id": record.transaction_id,
                "transaction_month": record.transaction_month,
                "transaction_month_index": month_index,
                "resale_price": float(record.resale_price),
                "town": str(record.town).strip().upper(),
                "flat_type": _flat_type_key(record.flat_type) or str(record.flat_type).strip().upper(),
                "flat_model": str(record.flat_model or "__MISSING__").strip().upper(),
                "floor_area_sqm": float(record.floor_area_sqm),
                "storey_midpoint": _storey_midpoint(record.storey_range),
                "lease_commencement": float(record.lease_commencement),
                "remaining_lease_months_at_transaction": float(
                    max(0, (record.lease_commencement + 99) * 12 - month_index)
                ),
            }
        )
    return pd.DataFrame(rows)


def _listing_frame(listing: ConfirmedListing, town: str | None, valuation_date: date) -> pd.DataFrame:
    month_index = valuation_date.year * 12 + valuation_date.month
    lease_months = listing.remaining_lease_months
    if lease_months is None and listing.remaining_lease_years is not None:
        lease_months = round(listing.remaining_lease_years * 12)
    return pd.DataFrame(
        [
            {
                "floor_area_sqm": float(listing.floor_area_sqm),
                "storey_midpoint": _storey_midpoint(listing.storey_range),
                "lease_commencement": float(listing.lease_commencement_year or np.nan),
                "remaining_lease_months_at_transaction": float(lease_months or np.nan),
                "transaction_month_index": month_index,
                "town": str(town or "__MISSING__").strip().upper(),
                "flat_type": _flat_type_key(listing.flat_type) or str(listing.flat_type).strip().upper(),
                "flat_model": str(listing.flat_model or "__MISSING__").strip().upper(),
            }
        ]
    )


def _prepare_frame(frame: pd.DataFrame, numeric_medians: pd.Series) -> pd.DataFrame:
    result = frame.copy()
    result[NUMERIC_FEATURES] = (
        result[NUMERIC_FEATURES].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    )
    result[NUMERIC_FEATURES] = result[NUMERIC_FEATURES].fillna(numeric_medians).fillna(0.0)
    for column in CATEGORICAL_FEATURES:
        result[column] = result[column].fillna("__MISSING__").astype(str)
    return result


def _numeric_medians(frame: pd.DataFrame) -> pd.Series:
    return frame[NUMERIC_FEATURES].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).median()


def _snapshot_key(frame: pd.DataFrame) -> str:
    values = frame[["transaction_id", "transaction_month", "resale_price"]].to_csv(index=False).encode()
    return hashlib.sha256(values).hexdigest()


def _storey_midpoint(value: object) -> float:
    numbers = [int(number) for number in re.findall(r"\d+", str(value or ""))]
    return float(sum(numbers) / len(numbers)) if numbers else np.nan
