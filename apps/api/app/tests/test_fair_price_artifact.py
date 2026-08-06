"""The CatBoost artifact is train-time only; inference never calls fit."""

from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd

from app.adapters.factory import get_transactions_adapter
from app.core.config import settings
from app.domain.models import ConfirmedListing
from app.engines import fair_price_catboost as catboost


def _listing() -> ConfirmedListing:
    return ConfirmedListing(
        listing_id=uuid4(),
        session_id=uuid4(),
        display_name="Bishan flat",
        asking_price=700_000,
        floor_area_sqm=90.0,
        address="123 Bishan Street 12",
        flat_type="4 ROOM",
        remaining_lease_years=65.0,
        lease_commencement_year=1991,
    )


class _FakeModel:
    def save_model(self, path: str) -> None:
        with open(path, "w") as output:
            output.write("fake model")

    def load_model(self, _path: str) -> None:
        return None

    def predict(self, _frame):
        return [700_000.0]


def _fake_fitted() -> catboost._FittedCatBoost:
    return catboost._FittedCatBoost(
        model=_FakeModel(),
        numeric_medians=pd.Series({column: 1.0 for column in catboost.NUMERIC_FEATURES}),
        residual_low=-10_000.0,
        residual_high=10_000.0,
        training_rows=10,
        calibration_rows=5,
        calibration_source="test",
        supported_flat_types=frozenset({"4 ROOM"}),
    )


def _install_fake_catboost(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "catboost", SimpleNamespace(CatBoostRegressor=_FakeModel))


def test_missing_artifact_never_trains_during_prediction(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "fair_price_model_artifact_path", str(tmp_path / "missing"))
    monkeypatch.setattr(catboost, "_fit", lambda _history: (_ for _ in ()).throw(AssertionError("must not fit")))
    assert catboost.predict(_listing(), "BISHAN", date.today(), get_transactions_adapter().all_records()) is None


def test_missing_artifact_file_falls_back_without_training(monkeypatch, tmp_path) -> None:
    artifact_dir = tmp_path / "artifact"
    records = get_transactions_adapter().all_records()
    monkeypatch.setattr(catboost, "_fit", lambda _history: _fake_fitted())
    catboost.train_artifact(records, str(artifact_dir))
    (artifact_dir / "model.cbm").unlink()
    monkeypatch.setattr(settings, "fair_price_model_artifact_path", str(artifact_dir))
    monkeypatch.setattr(catboost, "_fit", lambda _history: (_ for _ in ()).throw(AssertionError("must not fit")))
    catboost._clear_artifact_cache()

    assert catboost.predict(_listing(), "BISHAN", date.today(), records) is None


def test_artifact_loads_once_and_repeated_predictions_match(monkeypatch, tmp_path) -> None:
    artifact_dir = tmp_path / "artifact"
    records = get_transactions_adapter().all_records()
    monkeypatch.setattr(catboost, "_fit", lambda _history: _fake_fitted())
    _install_fake_catboost(monkeypatch)
    catboost.train_artifact(records, str(artifact_dir))
    monkeypatch.setattr(settings, "fair_price_model_artifact_path", str(artifact_dir))
    catboost._clear_artifact_cache()

    first = catboost.predict(_listing(), "BISHAN", date.today(), records)
    second = catboost.predict(_listing(), "BISHAN", date.today(), records)

    assert first is not None
    assert first == second
    assert len(catboost._ARTIFACT_CACHE) == 1


def test_incompatible_artifact_falls_back_without_training(monkeypatch, tmp_path) -> None:
    artifact_dir = tmp_path / "artifact"
    records = get_transactions_adapter().all_records()
    monkeypatch.setattr(catboost, "_fit", lambda _history: _fake_fitted())
    catboost.train_artifact(records, str(artifact_dir))
    (artifact_dir / "metadata.json").write_text('{"model_version":"wrong"}')
    monkeypatch.setattr(settings, "fair_price_model_artifact_path", str(artifact_dir))
    monkeypatch.setattr(catboost, "_fit", lambda _history: (_ for _ in ()).throw(AssertionError("must not fit")))
    catboost._clear_artifact_cache()

    assert catboost.predict(_listing(), "BISHAN", date.today(), records) is None


def test_relative_artifact_path_is_resolved_from_repo_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(catboost, "_REPO_ROOT", tmp_path)
    artifact_dir = tmp_path / "artifact"
    records = get_transactions_adapter().all_records()
    monkeypatch.setattr(catboost, "_fit", lambda _history: _fake_fitted())
    _install_fake_catboost(monkeypatch)
    catboost.train_artifact(records, str(artifact_dir))
    monkeypatch.setattr(settings, "fair_price_model_artifact_path", "artifact")
    monkeypatch.chdir(tmp_path)
    catboost._clear_artifact_cache()

    assert catboost.predict(_listing(), "BISHAN", date.today(), records) is not None


def test_invalid_metadata_falls_back_without_training(monkeypatch, tmp_path) -> None:
    artifact_dir = tmp_path / "artifact"
    records = get_transactions_adapter().all_records()
    monkeypatch.setattr(catboost, "_fit", lambda _history: _fake_fitted())
    catboost.train_artifact(records, str(artifact_dir))
    (artifact_dir / "metadata.json").write_text("not json")
    monkeypatch.setattr(settings, "fair_price_model_artifact_path", str(artifact_dir))
    monkeypatch.setattr(catboost, "_fit", lambda _history: (_ for _ in ()).throw(AssertionError("must not fit")))
    catboost._clear_artifact_cache()

    assert catboost.predict(_listing(), "BISHAN", date.today(), records) is None
