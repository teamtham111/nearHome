from __future__ import annotations

import numpy as np
import pandas as pd

from app.evaluation.benchmark import (
    _fit_ml_models,
    _hybrids,
    audit_experiment_spec,
    design_experiment,
)
from app.evaluation.data import temporal_split
from app.evaluation.metrics import regression_metrics, segment_metrics


def _rows(count: int = 24) -> pd.DataFrame:
    dates = pd.date_range("2022-01-01", periods=count, freq="MS")
    return pd.DataFrame(
        {
            "row_id": [f"r{i}" for i in range(count)],
            "transaction_date": dates,
            "transaction_month_index": dates.year * 12 + dates.month,
            "resale_price": np.linspace(400_000, 600_000, count),
            "town": ["BISHAN" if i % 2 else "TAMPINES" for i in range(count)],
            "flat_type": ["4 ROOM"] * count,
            "flat_model": ["IMPROVED" if i % 3 else "MODEL A" for i in range(count)],
            "block": [str(100 + i % 4) for i in range(count)],
            "street": ["BISHAN ST 12"] * count,
            "address_key": [("100", "BISHAN STREET 12")] * count,
            "floor_area_sqm": np.linspace(80, 100, count),
            "storey_midpoint": [5.0] * count,
            "lease_commencement": [1990] * count,
            "remaining_lease_months_at_transaction": [780] * count,
            "comparable_count": [8] * count,
            "comparable_level": [0] * count,
            "comparable_fallback": ["same_town_flat_type"] * count,
            "transaction_period": ["2022Q1"] * count,
            "price_band": ["300-500k"] * count,
            "floor_area_band": ["60-90"] * count,
            "remaining_lease_band": [">=70y"] * count,
        }
    )


def test_temporal_split_assigns_each_row_once():
    frame = _rows(36)
    split = temporal_split(frame, validation_months=6, final_test_months=6)
    ids = [set(part.row_id) for part in (split.train, split.validation, split.final_test)]
    assert not ids[0] & ids[1] & ids[2]
    assert sum(map(len, ids)) == len(frame)
    assert max(split.train.transaction_date) < min(split.validation.transaction_date)
    assert max(split.validation.transaction_date) < min(split.final_test.transaction_date)


def test_experiment_audit_rejects_target_feature_leakage():
    spec = design_experiment()
    spec.feature_matrix["linear_regression"]["numeric"].append("resale_price")
    audit = audit_experiment_spec(spec)
    assert audit["status"] == "FAIL"
    assert any("leakage" in issue.lower() for issue in audit["blocking_issues"])


def test_catboost_pipeline_handles_missing_and_unseen_categories():
    train = _rows(18).copy()
    target = _rows(6).copy()
    target["town"] = "NEW TOWN"
    target["flat_model"] = None
    target.loc[0, "floor_area_sqm"] = np.inf
    predictions, configurations, failures = _fit_ml_models(train, target, seed=42, iterations=10)
    assert "catboost" in predictions
    assert len(predictions["catboost"]) == len(target)
    assert np.isfinite(predictions["catboost"]).all()
    assert configurations["catboost"]["preprocessing_fit_rows"] == len(train)
    assert not [failure for failure in failures if failure["model"] == "catboost"]


def test_hybrid_weight_is_selected_from_validation_and_applied_to_final():
    validation = _rows(8).copy()
    final = _rows(8).copy()
    validation["weighted_comparables"] = (
        validation.actual_price if "actual_price" in validation else validation.resale_price
    )
    final["weighted_comparables"] = final.resale_price
    validation["actual_price"] = validation.resale_price
    final["actual_price"] = final.resale_price
    validation["catboost"] = validation.actual_price + 100_000
    final["catboost"] = final.actual_price + 100_000
    hybrid, choices = _hybrids(validation, final)
    assert choices["selection_split"] == "validation_only"
    assert set(hybrid.model) == {"weighted_average_hybrid", "gated_hybrid"}


def test_metrics_and_segments_include_sample_counts():
    frame = _rows(8).copy()
    frame["actual_price"] = frame.resale_price
    frame["model"] = frame.actual_price * 1.05
    metrics = regression_metrics(frame.actual_price, frame.model)
    assert metrics["evaluated_rows"] == 8
    segments = segment_metrics(frame.rename(columns={"model": "weighted_comparables"}), ["weighted_comparables"])
    assert not segments.empty
    assert (segments["sample_size"] > 0).all()
