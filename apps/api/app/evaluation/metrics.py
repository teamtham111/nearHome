"""Deterministic regression metrics and segment diagnostics."""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
import pandas as pd


def regression_metrics(actual: Iterable[float], predicted: Iterable[float]) -> dict[str, float | int | None]:
    y_true = np.asarray(list(actual), dtype=float)
    y_pred = np.asarray(list(predicted), dtype=float)
    valid = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true = y_true[valid]
    y_pred = y_pred[valid]
    if not len(y_true):
        return {
            "mae": None,
            "median_absolute_error": None,
            "rmse": None,
            "mape": None,
            "within_5_pct": None,
            "within_10_pct": None,
            "prediction_coverage": 0.0,
            "evaluated_rows": 0,
        }
    absolute = np.abs(y_true - y_pred)
    percentage = absolute / np.maximum(np.abs(y_true), 1.0)
    return {
        "mae": float(np.mean(absolute)),
        "median_absolute_error": float(np.median(absolute)),
        "rmse": float(math.sqrt(np.mean((y_true - y_pred) ** 2))),
        "mape": float(np.mean(percentage) * 100),
        "within_5_pct": float(np.mean(percentage <= 0.05) * 100),
        "within_10_pct": float(np.mean(percentage <= 0.10) * 100),
        "prediction_coverage": 1.0,
        "evaluated_rows": int(len(y_true)),
    }


def segment_metrics(predictions: pd.DataFrame, model_columns: list[str]) -> pd.DataFrame:
    segments = {
        "town": "town",
        "flat_type": "flat_type",
        "transaction_period": "transaction_period",
        "price_band": "price_band",
        "floor_area_band": "floor_area_band",
        "remaining_lease_band": "remaining_lease_band",
    }
    rows: list[dict[str, object]] = []
    for segment_name, segment_column in segments.items():
        for segment_value, group in predictions.groupby(segment_column, dropna=False):
            for model in model_columns:
                metrics = regression_metrics(group["actual_price"], group[model])
                rows.append(
                    {
                        "segment": segment_name,
                        "segment_value": str(segment_value),
                        "model": model,
                        "sample_size": len(group),
                        **metrics,
                    }
                )
    return pd.DataFrame(rows)


def add_segments(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["transaction_period"] = result["transaction_date"].dt.to_period("Q").astype(str)
    result["price_band"] = pd.cut(
        result["actual_price"],
        bins=[0, 300_000, 500_000, 700_000, 1_000_000, np.inf],
        labels=["<300k", "300-500k", "500-700k", "700k-1m", ">=1m"],
        include_lowest=True,
    ).astype(str)
    result["floor_area_band"] = pd.cut(
        result["floor_area_sqm"],
        bins=[0, 60, 90, 120, 150, np.inf],
        labels=["<60", "60-90", "90-120", "120-150", ">=150"],
        include_lowest=True,
    ).astype(str)
    result["remaining_lease_band"] = pd.cut(
        result["remaining_lease_months_at_transaction"],
        bins=[0, 600, 720, 840, np.inf],
        labels=["<50y", "50-60y", "60-70y", ">=70y"],
        include_lowest=True,
    ).astype(str)
    return result
