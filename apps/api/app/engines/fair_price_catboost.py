"""Production CatBoost fair-price estimator.

The estimator deliberately shares the feature definitions and hyperparameters
used by the offline challenger benchmark. Training data is restricted to
transactions before the valuation month, so a historical valuation cannot see
future prices. The model is cached for the immutable transaction snapshot and
the prediction interval is calibrated on the latest historical holdout.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.adapters.base import TransactionRecord
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.models import ConfirmedListing
from app.engines.fair_price_comparables import _flat_type_key
from app.evaluation.data import CATEGORICAL_FEATURES, NUMERIC_FEATURES

logger = get_logger(__name__)

MODEL_VERSION = "catboost_v2_artifact"
_ITERATIONS = 400
_SEED = 42
_ARTIFACT_CACHE: dict[str, _FittedCatBoost] = {}
_REPO_ROOT = Path(__file__).resolve().parents[4]


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

    fitted = load_artifact(history)
    if fitted is None:
        return None

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


def train_artifact(
    records: list[TransactionRecord], artifact_dir: str, valuation_date: date | None = None
) -> dict[str, object]:
    """Train outside the request path and save a reproducible model artifact."""
    valuation_month = (valuation_date or date.today()).strftime("%Y-%m")
    history = _records_to_frame(records, before_month=valuation_month)
    if history.empty:
        raise ValueError("No transaction rows are available for CatBoost artifact training")
    fitted = _fit(history)
    target = resolve_artifact_dir(artifact_dir)
    metadata = {
        "model_version": MODEL_VERSION,
        "transaction_snapshot_key": _snapshot_key(history),
        "feature_columns": CATEGORICAL_FEATURES + NUMERIC_FEATURES,
        "numeric_medians": {key: float(value) for key, value in fitted.numeric_medians.items()},
        "residual_low": fitted.residual_low,
        "residual_high": fitted.residual_high,
        "training_rows": fitted.training_rows,
        "calibration_rows": fitted.calibration_rows,
        "calibration_source": fitted.calibration_source,
        "supported_flat_types": sorted(fitted.supported_flat_types),
        "training_cutoff_month": valuation_month,
    }
    _publish_artifact(target, fitted.model, metadata)
    _ARTIFACT_CACHE.pop(str(target), None)
    logger.info("fair_price_model_artifact_trained", extra={"artifact_path": str(target)})
    return {"artifact_dir": str(target), **metadata}


def load_artifact(history: pd.DataFrame) -> _FittedCatBoost | None:
    """Load a compatible prebuilt artifact once; never fit during inference."""
    configured_path = settings.fair_price_model_artifact_path.strip()
    if not configured_path:
        logger.warning("fair_price_model_artifact_missing", reason="path_not_configured")
        return None
    root = resolve_artifact_dir(configured_path)
    cache_key = str(root)
    cached = _ARTIFACT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    model_path = root / "model.cbm"
    metadata_path = root / "metadata.json"
    if not model_path.is_file() or not metadata_path.is_file():
        logger.warning(
            "fair_price_model_artifact_missing",
            reason="files_not_present",
            artifact_path=str(root),
        )
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        required_features = CATEGORICAL_FEATURES + NUMERIC_FEATURES
        if (
            metadata.get("model_version") != MODEL_VERSION
            or metadata.get("transaction_snapshot_key") != _snapshot_key(history)
            or metadata.get("feature_columns") != required_features
        ):
            logger.warning(
                "fair_price_model_artifact_incompatible",
                reason="model_or_snapshot_mismatch",
                artifact_path=str(root),
            )
            return None

        numeric_medians = pd.Series(metadata["numeric_medians"], dtype=float)
        if set(NUMERIC_FEATURES) - set(numeric_medians.index) or not np.isfinite(numeric_medians).all():
            raise ValueError("numeric medians are incomplete or invalid")
        residual_low = float(metadata["residual_low"])
        residual_high = float(metadata["residual_high"])
        training_rows = int(metadata["training_rows"])
        calibration_rows = int(metadata["calibration_rows"])
        supported_flat_types = frozenset(str(value) for value in metadata["supported_flat_types"])
        if (
            not np.isfinite([residual_low, residual_high]).all()
            or residual_low > residual_high
            or training_rows <= 0
            or calibration_rows < 0
            or not supported_flat_types
        ):
            raise ValueError("artifact metadata values are invalid")
        from catboost import CatBoostRegressor

        model = CatBoostRegressor()
        model.load_model(str(model_path))
        fitted = _FittedCatBoost(
            model=model,
            numeric_medians=numeric_medians,
            residual_low=residual_low,
            residual_high=residual_high,
            training_rows=training_rows,
            calibration_rows=calibration_rows,
            calibration_source=str(metadata["calibration_source"]),
            supported_flat_types=supported_flat_types,
        )
    except Exception as exc:  # Provider/model parsing must never break enrichment.
        logger.warning(
            "fair_price_model_artifact_invalid",
            error_type=type(exc).__name__,
            artifact_path=str(root),
        )
        return None
    _ARTIFACT_CACHE[cache_key] = fitted
    logger.info("fair_price_model_artifact_loaded", artifact_path=str(root))
    return fitted


def resolve_artifact_dir(value: str) -> Path:
    """Resolve local relative artifact paths from the repository root."""

    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (_REPO_ROOT / path).resolve()


def _publish_artifact(target: Path, model: Any, metadata: dict[str, object]) -> None:
    """Stage both artifact files, then atomically replace each published file."""

    target.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".catboost-build-", dir=target.parent))
    try:
        staged_model = staging / "model.cbm"
        staged_metadata = staging / "metadata.json"
        model.save_model(str(staged_model))
        staged_metadata.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        os.replace(staged_model, target / "model.cbm")
        os.replace(staged_metadata, target / "metadata.json")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _clear_artifact_cache() -> None:
    """Test-only cache reset; runtime has no retraining hook."""
    _ARTIFACT_CACHE.clear()


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


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Train the immutable NearHome CatBoost artifact")
    parser.add_argument("train", nargs="?", choices=["train"], default="train")
    parser.add_argument("--artifact-dir", default="artifacts/fair_price/catboost")
    args = parser.parse_args()
    from app.adapters.factory import get_transactions_adapter

    outcome = train_artifact(get_transactions_adapter().all_records(), args.artifact_dir)
    print(json.dumps({key: outcome[key] for key in ("artifact_dir", "model_version", "training_rows")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
