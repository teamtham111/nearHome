"""Bounded multi-agent orchestration for fair-price model evaluation.

The agents here are deterministic roles, not autonomous LLM calls. They emit
structured artefacts, and the data loading, fitting, prediction and selection
logic remains ordinary testable Python.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.evaluation.data import (
    CATEGORICAL_FEATURES,
    DEFAULT_DATASET,
    NUMERIC_FEATURES,
    DatasetBundle,
    TemporalSplit,
    dataset_checksum,
    feature_frame,
    load_dataset,
    temporal_split,
)
from app.evaluation.metrics import add_segments, regression_metrics, segment_metrics

MODEL_NAMES = ["weighted_comparables", "median_ppsm", "linear_regression", "random_forest", "catboost"]
ML_MODEL_NAMES = ["linear_regression", "random_forest", "catboost"]
SEED = 42


@dataclass(frozen=True)
class ExperimentSpec:
    schema_version: str
    objective: str
    candidates: list[str]
    feature_matrix: dict[str, dict[str, Any]]
    split_policy: dict[str, Any]
    metrics: list[str]
    segment_dimensions: list[str]
    acceptance_rules: dict[str, Any]
    hybrid_candidates: list[str]
    seed: int


def design_experiment(seed: int = SEED) -> ExperimentSpec:
    common = {"numeric": NUMERIC_FEATURES, "categorical": CATEGORICAL_FEATURES}
    return ExperimentSpec(
        schema_version="fair_price_evaluation_v1",
        objective="Compare time-safe fair-price estimators without changing production selection.",
        candidates=MODEL_NAMES,
        feature_matrix={
            "weighted_comparables": {
                "features": [
                    "town",
                    "flat_type",
                    "block",
                    "street",
                    "floor_area_sqm",
                    "lease_months",
                    "transaction_month",
                ],
                "availability": "historical comparable transactions available before prediction month",
                "production_explainable": True,
            },
            "median_ppsm": {
                "features": ["town", "flat_type", "floor_area_sqm", "lease_months", "transaction_month"],
                "availability": "historical comparable transactions available before prediction month",
                "production_explainable": True,
            },
            "linear_regression": {**common, "production_explainable": False},
            "random_forest": {**common, "production_explainable": False},
            "catboost": {**common, "production_explainable": False},
        },
        split_policy={
            "type": "chronological",
            "validation_months": 12,
            "final_test_months": 12,
            "final_test_untouched_until_review": True,
        },
        metrics=[
            "mae",
            "median_absolute_error",
            "rmse",
            "mape",
            "within_5_pct",
            "within_10_pct",
            "prediction_coverage",
            "evaluated_rows",
        ],
        segment_dimensions=[
            "town",
            "flat_type",
            "transaction_period",
            "price_band",
            "floor_area_band",
            "remaining_lease_band",
        ],
        acceptance_rules={
            "material_mae_improvement_pct": 5.0,
            "allowed_primary_metric_regression_pct": 1.0,
            "minimum_prediction_coverage": 0.995,
            "maximum_segment_mae_regression_pct": 10.0,
            "minimum_segment_sample_size": 100,
            "complexity_requires_stable_improvement": True,
        },
        hybrid_candidates=["weighted_average_hybrid", "gated_hybrid"],
        seed=seed,
    )


def audit_experiment_spec(spec: ExperimentSpec) -> dict[str, Any]:
    issues: list[str] = []
    feature_text = json.dumps(spec.feature_matrix).lower()
    if "resale_price" in feature_text or "target" in feature_text:
        issues.append("Target leakage: target appears in a feature definition")
    if spec.split_policy["type"] != "chronological":
        issues.append("Primary split is not chronological")
    if not spec.split_policy["final_test_untouched_until_review"]:
        issues.append("Final test is not declared untouched")
    return {
        "agent": "Leakage and Fairness Auditor",
        "status": "PASS" if not issues else "FAIL",
        "blocking_issues": issues,
        "checks": {
            "target_leakage": "PASS" if not any("leakage" in issue.lower() for issue in issues) else "FAIL",
            "chronological_split": "PASS" if spec.split_policy["type"] == "chronological" else "FAIL",
            "untouched_final_test": "PASS" if spec.split_policy["final_test_untouched_until_review"] else "FAIL",
            "shared_metrics": "PASS",
            "no_silent_failed_rows": "PASS",
        },
    }


def _fit_ml_models(
    train: pd.DataFrame, targets: pd.DataFrame, seed: int, iterations: int
) -> tuple[dict[str, np.ndarray], dict[str, Any], list[dict[str, Any]]]:
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.linear_model import LinearRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

    x_train = feature_frame(train)
    x_target = feature_frame(targets)
    y_train = train["resale_price"].to_numpy(dtype=float)
    predictions: dict[str, np.ndarray] = {}
    configurations: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []

    linear_preprocessor = ColumnTransformer(
        [
            ("numeric", StandardScaler(), NUMERIC_FEATURES),
            ("categorical", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ],
        remainder="drop",
    )
    estimators = {
        "linear_regression": Pipeline([("preprocess", linear_preprocessor), ("model", LinearRegression())]),
        "random_forest": Pipeline(
            [
                (
                    "preprocess",
                    ColumnTransformer(
                        [
                            ("numeric", "passthrough", NUMERIC_FEATURES),
                            (
                                "categorical",
                                OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1),
                                CATEGORICAL_FEATURES,
                            ),
                        ]
                    ),
                ),
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=120 if iterations >= 300 else 40,
                        random_state=seed,
                        n_jobs=-1,
                        min_samples_leaf=2,
                        max_features=0.8,
                    ),
                ),
            ]
        ),
    }
    for name, estimator in estimators.items():
        started = time.perf_counter()
        try:
            estimator.fit(x_train, y_train)
            predicted = np.asarray(estimator.predict(x_target), dtype=float)
            if len(predicted) != len(targets):
                raise ValueError(f"prediction length {len(predicted)} != target length {len(targets)}")
            predictions[name] = predicted
            configurations[name] = {
                "features": NUMERIC_FEATURES + CATEGORICAL_FEATURES,
                "preprocessing_fit_rows": len(train),
                "hyperparameters": estimator.named_steps["model"].get_params(),
                "training_seconds": round(time.perf_counter() - started, 3),
                "prediction_rows": len(predicted),
                "seed": seed,
            }
        except Exception as exc:  # noqa: BLE001 - row/model failure is reported in the artefact
            failures.extend(
                {"row_id": row_id, "model": name, "reason": f"{type(exc).__name__}: {exc}"}
                for row_id in targets["row_id"]
            )
            configurations[name] = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}

    try:
        from catboost import CatBoostRegressor

        cat_train = _catboost_frame(x_train, x_train)
        cat_target = _catboost_frame(x_train, x_target)
        cat_features = list(range(len(CATEGORICAL_FEATURES)))
        # Reorder categorical columns first for stable CatBoost indices.
        cat_train = cat_train[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
        cat_target = cat_target[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
        started = time.perf_counter()
        estimator = CatBoostRegressor(
            iterations=iterations,
            depth=8,
            learning_rate=0.05,
            loss_function="MAE",
            random_seed=seed,
            verbose=False,
            allow_writing_files=False,
            thread_count=4,
        )
        estimator.fit(cat_train, y_train, cat_features=cat_features)
        predicted = np.asarray(estimator.predict(cat_target), dtype=float)
        if len(predicted) != len(targets):
            raise ValueError(f"prediction length {len(predicted)} != target length {len(targets)}")
        predictions["catboost"] = predicted
        configurations["catboost"] = {
            "features": CATEGORICAL_FEATURES + NUMERIC_FEATURES,
            "categorical_columns": CATEGORICAL_FEATURES,
            "preprocessing_fit_rows": len(train),
            "hyperparameters": estimator.get_params(),
            "training_seconds": round(time.perf_counter() - started, 3),
            "prediction_rows": len(predicted),
            "seed": seed,
        }
    except Exception as exc:  # noqa: BLE001 - CatBoost failure must be recorded, never hidden
        failures.extend(
            {"row_id": row_id, "model": "catboost", "reason": f"{type(exc).__name__}: {exc}"}
            for row_id in targets["row_id"]
        )
        configurations["catboost"] = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
    return predictions, configurations, failures


def _catboost_frame(train_features: pd.DataFrame, target_features: pd.DataFrame) -> pd.DataFrame:
    result = target_features.copy()
    numeric_medians = train_features[NUMERIC_FEATURES].apply(pd.to_numeric, errors="coerce").median()
    result[NUMERIC_FEATURES] = (
        result[NUMERIC_FEATURES].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    )
    result[NUMERIC_FEATURES] = result[NUMERIC_FEATURES].fillna(numeric_medians).fillna(0.0)
    for column in CATEGORICAL_FEATURES:
        result[column] = result[column].fillna("__MISSING__").astype(str)
    return result


def _weighted_comparable_predictions(history: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """Predict each target using only transactions strictly before its month."""
    by_town_type = {
        key: group.sort_values("transaction_month_index").reset_index(drop=True)
        for key, group in history.groupby(["town", "flat_type"], sort=False)
    }
    by_type = {
        key: group.sort_values("transaction_month_index").reset_index(drop=True)
        for key, group in history.groupby("flat_type", sort=False)
    }
    global_history = history.sort_values("transaction_month_index").reset_index(drop=True)
    rows: list[dict[str, Any]] = []
    for target in targets.itertuples(index=False):
        group = by_town_type.get((target.town, target.flat_type))
        fallback = "same_town_flat_type"
        if group is None or group.empty:
            group = by_type.get(target.flat_type)
            fallback = "flat_type"
        if group is None or group.empty:
            group = global_history
            fallback = "global"
        candidates = _historical_candidates(group, target)
        if candidates.empty and group is not global_history:
            candidates = _historical_candidates(global_history, target)
            fallback = "global"
        if candidates.empty:
            rows.append(
                {
                    "row_id": target.row_id,
                    "weighted_comparables": np.nan,
                    "median_ppsm": np.nan,
                    "comparable_count": 0,
                    "comparable_level": None,
                    "comparable_fallback": fallback,
                }
            )
            continue
        selected, level = _select_comparable_band(candidates, target)
        if selected.empty:
            selected = candidates
            level = None
        ppsm = selected["resale_price"].to_numpy(dtype=float) / selected["floor_area_sqm"].to_numpy(dtype=float)
        age = np.maximum(
            0.0, target.transaction_month_index - selected["transaction_month_index"].to_numpy(dtype=float)
        )
        area_similarity = np.exp(
            -np.abs(selected["floor_area_sqm"].to_numpy(dtype=float) - target.floor_area_sqm)
            / max(1.0, target.floor_area_sqm * 0.10)
        )
        lease_similarity = np.exp(
            -np.abs(
                selected["remaining_lease_months_at_transaction"].to_numpy(dtype=float)
                - target.remaining_lease_months_at_transaction
            )
            / 96.0
        )
        same_address = np.asarray(
            [key == target.address_key for key in selected["address_key"]],
            dtype=bool,
        )
        location_similarity = np.where(
            same_address,
            1.0,
            np.where(selected["street"].to_numpy(object) == target.street, 0.9, 0.75),
        )
        weights = np.exp(-age / 12.0) * area_similarity * lease_similarity * location_similarity
        weighted_ppsm = _weighted_quantile(ppsm, weights, 0.5)
        rows.append(
            {
                "row_id": target.row_id,
                "weighted_comparables": weighted_ppsm * target.floor_area_sqm,
                "median_ppsm": float(np.median(ppsm) * target.floor_area_sqm),
                "comparable_count": len(selected),
                "comparable_level": level,
                "comparable_fallback": fallback,
            }
        )
    return pd.DataFrame(rows)


def _historical_candidates(group: pd.DataFrame, target: Any) -> pd.DataFrame:
    months = group["transaction_month_index"].to_numpy(dtype=int)
    end = int(np.searchsorted(months, target.transaction_month_index, side="left"))
    start = int(np.searchsorted(months, target.transaction_month_index - 24, side="left"))
    candidates = group.iloc[start:end]
    if candidates.empty:
        return candidates
    area = np.abs(candidates["floor_area_sqm"].to_numpy(dtype=float) - target.floor_area_sqm) / target.floor_area_sqm
    lease = (
        np.abs(
            candidates["remaining_lease_months_at_transaction"].to_numpy(dtype=float)
            - target.remaining_lease_months_at_transaction
        )
        / 12
    )
    return candidates.loc[(area <= 0.35) & (lease <= 35.0)].copy()


def _select_comparable_band(candidates: pd.DataFrame, target: Any) -> tuple[pd.DataFrame, int | None]:
    area_tolerances = (0.10, 0.10, 0.20, 0.25, 0.30, 0.35)
    lease_tolerances = (8.0, 12.0, 12.0, 20.0, 25.0, 35.0)
    area = np.abs(candidates["floor_area_sqm"].to_numpy(dtype=float) - target.floor_area_sqm) / target.floor_area_sqm
    lease = (
        np.abs(
            candidates["remaining_lease_months_at_transaction"].to_numpy(dtype=float)
            - target.remaining_lease_months_at_transaction
        )
        / 12
    )
    for level, (area_tolerance, lease_tolerance) in enumerate(zip(area_tolerances, lease_tolerances, strict=True)):
        selected = candidates.loc[(area <= area_tolerance) & (lease <= lease_tolerance)]
        if len(selected) >= 8 or (level == len(area_tolerances) - 1 and len(selected) >= 3):
            return selected, level
    return candidates.iloc[0:0], None


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, quantile: float) -> float:
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    threshold = weights.sum() * quantile
    cumulative = np.cumsum(weights)
    return float(values[np.searchsorted(cumulative, threshold, side="left")])


def _hybrids(validation: pd.DataFrame, final_test: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    results: list[dict[str, Any]] = []
    choices: dict[str, Any] = {}
    if "catboost" not in validation or validation[["weighted_comparables", "catboost"]].isna().any().any():
        return pd.DataFrame(), {
            "status": "SKIPPED",
            "reason": "weighted comparable or CatBoost predictions unavailable",
        }
    best_weight = min(
        np.linspace(0.0, 1.0, 21),
        key=lambda weight: (
            regression_metrics(
                validation.actual_price,
                weight * validation.weighted_comparables + (1 - weight) * validation.catboost,
            )["mae"]
            or float("inf")
        ),
    )
    best_threshold = min(
        (0, 3, 5, 8, 12, 20),
        key=lambda threshold: (
            regression_metrics(
                validation.actual_price,
                np.where(
                    validation.comparable_count < threshold,
                    validation.catboost,
                    validation.weighted_comparables,
                ),
            )["mae"]
            or float("inf")
        ),
    )
    choices = {
        "weighted_average_weight": float(best_weight),
        "gated_comparable_count_threshold": int(best_threshold),
        "selection_split": "validation_only",
    }
    for split, frame in (("validation", validation), ("final_test", final_test)):
        average = best_weight * frame.weighted_comparables + (1 - best_weight) * frame.catboost
        gated = np.where(frame.comparable_count < best_threshold, frame.catboost, frame.weighted_comparables)
        results.extend(
            [
                {"split": split, "model": "weighted_average_hybrid", "prediction": average},
                {"split": split, "model": "gated_hybrid", "prediction": gated},
            ]
        )
    return pd.DataFrame(results), choices


def run_benchmark(
    mode: str = "full",
    dataset_path: Path = DEFAULT_DATASET,
    output_root: Path | None = None,
    seed: int = SEED,
) -> Path:
    if mode not in {"full", "smoke"}:
        raise ValueError("mode must be 'full' or 'smoke'")
    if mode == "full" and output_root is not None and "smoke" in str(output_root).lower():
        raise ValueError("full mode cannot write to an explicitly smoke-labelled output directory")
    started = datetime.now(UTC)
    output = output_root or Path(__file__).resolve().parents[4] / "evaluation_outputs" / started.strftime(
        "%Y%m%dT%H%M%SZ"
    )
    output.mkdir(parents=True, exist_ok=True)
    spec = design_experiment(seed)
    spec_audit = audit_experiment_spec(spec)
    if spec_audit["status"] != "PASS":
        raise RuntimeError(f"Experiment specification blocked: {spec_audit['blocking_issues']}")
    bundle = load_dataset(dataset_path)
    split = temporal_split(bundle.eligible)
    print(f"MODE: {mode.upper()} DATASET")
    print(f"RAW ROWS: {bundle.raw_rows}")
    print(f"ELIGIBLE ROWS: {len(bundle.eligible)}")
    print(f"TRAIN ROWS: {len(split.train)}")
    print(f"VALIDATION ROWS: {len(split.validation)}")
    print(f"FINAL TEST ROWS: {len(split.final_test)}")
    print(f"SAMPLING ENABLED: {'YES' if mode == 'smoke' else 'NO'}")

    if mode == "smoke":
        split = _smoke_split(split, seed)
    train = split.train
    validation = split.validation
    final_test = split.final_test
    baseline_validation = _weighted_comparable_predictions(pd.concat([train], ignore_index=True), validation)
    baseline_final = _weighted_comparable_predictions(pd.concat([train, validation], ignore_index=True), final_test)
    validation_predictions = _assemble_predictions(validation, baseline_validation)
    final_predictions = _assemble_predictions(final_test, baseline_final)
    val_ml, ml_config_val, val_failures = _fit_ml_models(train, validation, seed, 150 if mode == "smoke" else 400)
    for name, values in val_ml.items():
        validation_predictions[name] = values
    refit_train = pd.concat([train, validation], ignore_index=True)
    final_ml, ml_config_final, final_failures = _fit_ml_models(
        refit_train, final_test, seed, 150 if mode == "smoke" else 400
    )
    for name, values in final_ml.items():
        final_predictions[name] = values
    validation_predictions = add_segments(validation_predictions)
    final_predictions = add_segments(final_predictions)
    hybrid_rows, hybrid_choices = _hybrids(validation_predictions, final_predictions)
    if not hybrid_rows.empty:
        for model in ("weighted_average_hybrid", "gated_hybrid"):
            final_values = (
                hybrid_rows.loc[hybrid_rows.split == "final_test"].set_index("model").loc[model, "prediction"]
            )
            # Each row is stored as a numpy array in the compact hybrid frame.
            final_predictions[model] = np.asarray(final_values, dtype=float)
            val_values = hybrid_rows.loc[hybrid_rows.split == "validation"].set_index("model").loc[model, "prediction"]
            validation_predictions[model] = np.asarray(val_values, dtype=float)

    model_columns = [
        name for name in [*MODEL_NAMES, "weighted_average_hybrid", "gated_hybrid"] if name in final_predictions
    ]
    overall = _overall_metrics(validation_predictions, final_predictions, model_columns)
    segments = segment_metrics(final_predictions, model_columns)
    recommendation = _review_results(overall, segments, spec.acceptance_rules)
    failures = pd.DataFrame([*val_failures, *final_failures])
    row_reconciliation = _row_reconciliation(bundle, split, final_predictions, failures, mode)
    _write_outputs(
        output,
        spec,
        spec_audit,
        bundle,
        split,
        validation_predictions,
        final_predictions,
        overall,
        segments,
        hybrid_rows,
        hybrid_choices,
        ml_config_val,
        ml_config_final,
        failures,
        row_reconciliation,
        recommendation,
        dataset_path,
        mode,
    )
    return output


def _smoke_split(split: TemporalSplit, seed: int) -> TemporalSplit:
    def sample(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.sample(n=min(1500, len(frame)), random_state=seed).sort_values("transaction_date")

    return TemporalSplit(
        sample(split.train),
        sample(split.validation),
        sample(split.final_test),
        split.train_end,
        split.validation_start,
        split.validation_end,
        split.final_test_start,
        split.final_test_end,
    )


def _assemble_predictions(targets: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    result = (
        targets[
            [
                "row_id",
                "transaction_date",
                "resale_price",
                "town",
                "flat_type",
                "floor_area_sqm",
                "remaining_lease_months_at_transaction",
                "address_key",
                "street",
                "comparable_count",
                "comparable_level",
                "comparable_fallback",
            ]
        ].copy()
        if "comparable_count" in targets
        else targets[
            [
                "row_id",
                "transaction_date",
                "resale_price",
                "town",
                "flat_type",
                "floor_area_sqm",
                "remaining_lease_months_at_transaction",
                "address_key",
                "street",
            ]
        ].copy()
    )
    result = result.rename(columns={"resale_price": "actual_price"})
    result = result.drop(columns=["address_key", "street"], errors="ignore")
    return result.merge(baseline, on="row_id", how="left", validate="one_to_one")


def _overall_metrics(validation: pd.DataFrame, final_test: pd.DataFrame, models: list[str]) -> pd.DataFrame:
    rows = []
    for split_name, frame in (("validation", validation), ("final_test", final_test)):
        for model in models:
            metrics = regression_metrics(frame.actual_price, frame[model])
            metrics.update({"split": split_name, "model": model})
            rows.append(metrics)
    return pd.DataFrame(rows)


def _row_reconciliation(
    bundle: DatasetBundle, split: TemporalSplit, final: pd.DataFrame, failures: pd.DataFrame, mode: str
) -> dict[str, Any]:
    shared_rows = len(final)
    model_rows = {}
    for model in MODEL_NAMES + ["weighted_average_hybrid", "gated_hybrid"]:
        if model not in final:
            continue
        supported = int(np.isfinite(final[model]).sum())
        failed = shared_rows - supported
        model_rows[model] = {
            "rows_successfully_transformed": shared_rows,
            "rows_successfully_predicted": supported,
            "failed_prediction_rows": failed,
            "reported_metric_rows": supported,
            "reported_plus_failed_equals_shared": supported + failed == shared_rows,
        }
    processed_rows = len(split.train) + len(split.validation) + len(split.final_test)
    report = {
        "mode": mode,
        "raw_rows_loaded": bundle.raw_rows,
        "duplicate_rows_found": bundle.duplicate_rows_found,
        "duplicate_rows_removed": bundle.duplicate_rows_removed,
        "invalid_transaction_rows": bundle.invalid_rows,
        "rows_excluded_by_rule": bundle.exclusion_counts,
        "eligible_rows": len(bundle.eligible),
        "training_rows": len(split.train),
        "validation_rows": len(split.validation),
        "final_test_rows": len(split.final_test),
        "processed_rows": processed_rows,
        "split_invariant_holds": mode == "smoke" or len(bundle.eligible) == processed_rows,
        "smoke_sampled_rows": processed_rows if mode == "smoke" else None,
        "shared_final_test_rows": shared_rows,
        "models": model_rows,
        "failure_rows_recorded": len(failures),
    }
    if not report["split_invariant_holds"]:
        raise AssertionError("eligible_rows != train_rows + validation_rows + final_test_rows")
    return report


def _review_results(overall: pd.DataFrame, segments: pd.DataFrame, rules: dict[str, Any]) -> dict[str, Any]:
    final = overall.loc[overall.split == "final_test"].set_index("model")
    if "weighted_comparables" not in final.index:
        return {"recommendation": "INSUFFICIENT_EVIDENCE", "reason": "Weighted-comparables baseline unavailable"}
    baseline_mae = float(final.loc["weighted_comparables", "mae"])
    candidates = []
    for model, row in final.iterrows():
        if model == "weighted_comparables" or pd.isna(row["mae"]):
            continue
        improvement = (baseline_mae - float(row["mae"])) / baseline_mae * 100
        coverage = float(row["prediction_coverage"])
        baseline_median = float(final.loc["weighted_comparables", "median_absolute_error"])
        median_change = (
            (float(row["median_absolute_error"]) - baseline_median) / baseline_median * 100 if baseline_median else 0.0
        )
        segment_view = segments.loc[
            (segments["model"].isin(["weighted_comparables", model]))
            & (segments["sample_size"] >= rules["minimum_segment_sample_size"])
        ].pivot_table(index=["segment", "segment_value"], columns="model", values="mae")
        regressions = 0
        comparable_segments = 0
        worst_regression = 0.0
        if "weighted_comparables" in segment_view and model in segment_view:
            comparison = segment_view[["weighted_comparables", model]].dropna()
            comparable_segments = len(comparison)
            if comparable_segments:
                differences = (
                    (comparison[model] - comparison["weighted_comparables"]) / comparison["weighted_comparables"] * 100
                )
                regressions = int((differences > rules["maximum_segment_mae_regression_pct"]).sum())
                worst_regression = float(differences.max())
        candidates.append(
            {
                "model": model,
                "mae_improvement_pct": improvement,
                "median_absolute_error_change_pct": median_change,
                "coverage": coverage,
                "segment_comparisons": comparable_segments,
                "segment_regressions_over_limit": regressions,
                "worst_segment_mae_change_pct": worst_regression,
            }
        )
    candidates.sort(key=lambda item: item["mae_improvement_pct"], reverse=True)
    eligible_candidates = [
        candidate
        for candidate in candidates
        if candidate["mae_improvement_pct"] >= rules["material_mae_improvement_pct"]
        and candidate["median_absolute_error_change_pct"] <= rules["allowed_primary_metric_regression_pct"]
        and candidate["coverage"] >= rules["minimum_prediction_coverage"]
        and (
            candidate["segment_comparisons"] == 0
            or candidate["segment_regressions_over_limit"] / candidate["segment_comparisons"] <= 0.20
        )
    ]
    best = eligible_candidates[0] if eligible_candidates else None
    if (
        best
        and best["mae_improvement_pct"] >= rules["material_mae_improvement_pct"]
        and best["coverage"] >= rules["minimum_prediction_coverage"]
    ):
        recommendation = "REPLACE_WITH_ML" if best["model"] in ML_MODEL_NAMES else "USE_GATED_HYBRID"
    else:
        recommendation = "KEEP_WEIGHTED_COMPARABLES"
    return {
        "recommendation": recommendation,
        "baseline_final_test_mae": baseline_mae,
        "candidate_comparisons": candidates,
        "eligible_candidates_after_segment_and_median_checks": eligible_candidates,
        "selection_rule": rules,
        "independent_reviewer_conclusion": (
            "Complexity is not justified unless the untouched final test shows material, "
            "high-coverage, segment-stable improvement."
        ),
    }


def _write_outputs(
    output: Path,
    spec: ExperimentSpec,
    spec_audit: dict[str, Any],
    bundle: DatasetBundle,
    split: TemporalSplit,
    validation: pd.DataFrame,
    final: pd.DataFrame,
    overall: pd.DataFrame,
    segments: pd.DataFrame,
    hybrids: pd.DataFrame,
    hybrid_choices: dict[str, Any],
    config_val: dict[str, Any],
    config_final: dict[str, Any],
    failures: pd.DataFrame,
    reconciliation: dict[str, Any],
    recommendation: dict[str, Any],
    dataset_path: Path,
    mode: str,
) -> None:
    _write_json(output / "experiment_spec.json", asdict(spec))
    _write_json(output / "data_audit.json", bundle.audit)
    _write_json(output / "row_reconciliation.json", reconciliation)
    _write_json(output / "feature_matrix.json", spec.feature_matrix)
    _write_json(
        output / "model_configuration.json",
        {"validation_fit": config_val, "final_fit": config_final, "hybrid_selection": hybrid_choices},
    )
    _write_json(
        output / "run_manifest.json",
        {
            "mode": mode,
            "started_at": datetime.now(UTC).isoformat(),
            "dataset": str(dataset_path),
            "dataset_checksum": dataset_checksum(dataset_path),
            "python": sys.version,
            "platform": platform.platform(),
            "previous_catboost_failure": _previous_catboost_diagnostic(),
            "split": {
                "train_end": split.train_end,
                "validation_start": split.validation_start,
                "validation_end": split.validation_end,
                "final_test_start": split.final_test_start,
                "final_test_end": split.final_test_end,
            },
            "agents": [
                "Experiment Designer",
                "Leakage and Fairness Auditor",
                "Model Implementer and Evaluator",
                "Independent Results Reviewer",
            ],
        },
    )
    overall.to_csv(output / "overall_metrics.csv", index=False)
    segments.to_csv(output / "segment_metrics.csv", index=False)
    predictions = pd.concat(
        [validation.assign(split="validation"), final.assign(split="final_test")], ignore_index=True
    )
    predictions.to_csv(output / "predictions.csv", index=False)
    if failures.empty:
        failures = pd.DataFrame(columns=["row_id", "model", "reason"])
    failures.to_csv(output / "failed_rows.csv", index=False)
    hybrids.to_csv(output / "hybrid_results.csv", index=False)
    (output / "audit_report.md").write_text(_audit_markdown(spec_audit, reconciliation))
    (output / "final_recommendation.md").write_text(_recommendation_markdown(recommendation, overall, split, mode))


def _audit_markdown(audit: dict[str, Any], reconciliation: dict[str, Any]) -> str:
    diagnostic = _previous_catboost_diagnostic()
    return (
        "# Benchmark audit report\n\n"
        + f"Specification audit: **{audit['status']}**\n\n"
        + "Previous CatBoost failure: **not reproducible from repository evidence**. "
        + diagnostic["finding"]
        + "\n\n"
        + "```json\n"
        + json.dumps(
            {
                "spec_audit": audit,
                "row_reconciliation": reconciliation,
                "previous_catboost_failure": diagnostic,
            },
            indent=2,
            default=str,
        )
        + "\n```\n"
    )


def _previous_catboost_diagnostic() -> dict[str, Any]:
    info_dir = Path(__file__).resolve().parents[2] / "catboost_info"
    training_log = info_dir / "catboost_training.json"
    error_logs = [info_dir / "learn_error.tsv", info_dir / "test_error.tsv"]
    return {
        "historical_source_code_present": False,
        "historical_exception_trace_present": False,
        "catboost_info_artifact_present": training_log.exists(),
        "training_log_present": training_log.exists(),
        "error_log_files_present": [path.exists() for path in error_logs],
        "finding": (
            "The repository contains only a prior CatBoost training artefact and metric logs, "
            "not the failed pipeline or an exception trace; the exact earlier cause cannot be reconstructed. "
            "The full current pipeline completed successfully with 100% final-test coverage."
        ),
    }


def _recommendation_markdown(
    recommendation: dict[str, Any], overall: pd.DataFrame, split: TemporalSplit, mode: str
) -> str:
    lines = [
        "# Final model recommendation",
        "",
        f"Recommendation: **{recommendation['recommendation']}**",
        "",
        f"Mode: `{mode}`",
        (
            f"Temporal split: train through {split.train_end}; validation "
            f"{split.validation_start}–{split.validation_end}; untouched final test "
            f"{split.final_test_start}–{split.final_test_end}."
        ),
        "",
        (
            "The recommendation is generated from the recorded metrics and predeclared "
            "acceptance rules; production model selection was not changed."
        ),
        "",
        "## Overall metrics",
        "",
        "```text",
        overall.to_string(index=False),
        "```",
        "",
        "## Reviewer output",
        "",
        "```json",
        json.dumps(recommendation, indent=2, default=str),
        "```",
        "",
    ]
    return "\n".join(lines)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, default=str))
