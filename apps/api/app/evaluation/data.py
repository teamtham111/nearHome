"""Full-dataset loading, eligibility, feature generation and temporal splits."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from app.utils.hdb_address import canonical_hdb_parts

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATASET = ROOT / "data_pipeline" / "fixtures" / "hdb_transactions.json"

NUMERIC_FEATURES = [
    "floor_area_sqm",
    "storey_midpoint",
    "lease_commencement",
    "remaining_lease_months_at_transaction",
    "transaction_month_index",
]
CATEGORICAL_FEATURES = ["town", "flat_type", "flat_model"]
TARGET_COLUMN = "resale_price"


@dataclass(frozen=True)
class DatasetBundle:
    raw_rows: int
    duplicate_rows_found: int
    duplicate_rows_removed: int
    invalid_rows: int
    exclusion_counts: dict[str, int]
    eligible: pd.DataFrame
    audit: dict[str, object]


@dataclass(frozen=True)
class TemporalSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    final_test: pd.DataFrame
    train_end: str
    validation_start: str
    validation_end: str
    final_test_start: str
    final_test_end: str


def load_dataset(path: Path = DEFAULT_DATASET) -> DatasetBundle:
    raw = json.loads(path.read_text())
    frame = pd.DataFrame(raw)
    raw_rows = len(frame)
    frame["source_index"] = np.arange(raw_rows, dtype=int)
    frame["row_id"] = frame.apply(
        lambda row: f"{row.get('transaction_id', 'missing')}:{int(row['source_index'])}", axis=1
    )

    data_columns = [column for column in frame.columns if column not in {"source_index", "row_id"}]
    duplicate_mask = frame.duplicated(subset=data_columns, keep="first")
    duplicate_rows_found = int(duplicate_mask.sum())
    frame = frame.loc[~duplicate_mask].copy()

    exclusions: dict[str, int] = {}
    months = pd.to_datetime(frame["transaction_month"].astype(str), format="%Y-%m", errors="coerce")
    numeric_columns = ["floor_area_sqm", "resale_price", "lease_commencement"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    required_text = ["town", "flat_type", "block", "street", "flat_model", "storey_range"]
    for column in required_text:
        frame[column] = frame[column].fillna("").astype(str).str.strip()

    rules = {
        "invalid_transaction_month": months.isna(),
        "non_positive_floor_area": frame["floor_area_sqm"].isna() | (frame["floor_area_sqm"] <= 0),
        "non_positive_resale_price": frame["resale_price"].isna() | (frame["resale_price"] <= 0),
        "missing_town": frame["town"].eq(""),
        "missing_flat_type": frame["flat_type"].eq(""),
        "missing_block": frame["block"].eq(""),
        "missing_street": frame["street"].eq(""),
        "invalid_lease_commencement": frame["lease_commencement"].isna()
        | (frame["lease_commencement"] < 1900)
        | (frame["lease_commencement"] > months.dt.year.fillna(9999)),
    }
    invalid_mask = pd.Series(False, index=frame.index)
    for name, mask in rules.items():
        exclusions[name] = int(mask.sum())
        invalid_mask |= mask

    eligible = frame.loc[~invalid_mask].copy()
    eligible["transaction_date"] = months.loc[eligible.index]
    eligible["transaction_month_index"] = (
        eligible["transaction_date"].dt.year * 12 + eligible["transaction_date"].dt.month
    ).astype(int)
    eligible["remaining_lease_months_at_transaction"] = (
        (eligible["lease_commencement"] + 99) * 12 - eligible["transaction_month_index"]
    ).clip(lower=0)
    eligible["storey_midpoint"] = eligible["storey_range"].map(_storey_midpoint)
    eligible["town"] = eligible["town"].str.upper()
    eligible["flat_type"] = eligible["flat_type"].str.upper()
    eligible["flat_model"] = eligible["flat_model"].replace("", "__MISSING__").str.upper()
    eligible["address_key"] = [
        canonical_hdb_parts(block, street) for block, street in zip(eligible["block"], eligible["street"], strict=True)
    ]
    eligible = eligible.sort_values(["transaction_date", "row_id"]).reset_index(drop=True)

    audit = {
        "dataset_path": str(path),
        "raw_rows_loaded": raw_rows,
        "duplicate_rows_found": duplicate_rows_found,
        "duplicate_rows_removed": duplicate_rows_found,
        "invalid_transaction_rows": int(invalid_mask.sum()),
        "rows_excluded_by_rule": exclusions,
        "eligible_rows": len(eligible),
    }
    return DatasetBundle(
        raw_rows=raw_rows,
        duplicate_rows_found=duplicate_rows_found,
        duplicate_rows_removed=duplicate_rows_found,
        invalid_rows=int(invalid_mask.sum()),
        exclusion_counts=exclusions,
        eligible=eligible,
        audit=audit,
    )


def temporal_split(frame: pd.DataFrame, validation_months: int = 12, final_test_months: int = 12) -> TemporalSplit:
    months = sorted(frame["transaction_date"].dt.to_period("M").unique())
    if len(months) <= validation_months + final_test_months:
        raise ValueError("Not enough distinct transaction months for train/validation/final-test splits")
    final_months = months[-final_test_months:]
    validation_months_values = months[-final_test_months - validation_months : -final_test_months]
    train_months = months[: -validation_months - final_test_months]
    masks = [
        frame["transaction_date"].dt.to_period("M").isin(train_months),
        frame["transaction_date"].dt.to_period("M").isin(validation_months_values),
        frame["transaction_date"].dt.to_period("M").isin(final_months),
    ]
    train, validation, final_test = (frame.loc[mask].copy() for mask in masks)
    result = TemporalSplit(
        train=train,
        validation=validation,
        final_test=final_test,
        train_end=str(train_months[-1]),
        validation_start=str(validation_months_values[0]),
        validation_end=str(validation_months_values[-1]),
        final_test_start=str(final_months[0]),
        final_test_end=str(final_months[-1]),
    )
    assigned = len(train) + len(validation) + len(final_test)
    if (
        assigned != len(frame)
        or set(train.row_id) & set(validation.row_id)
        or set(train.row_id) & set(final_test.row_id)
    ):
        raise AssertionError("Temporal split does not assign every eligible row exactly once")
    if set(validation.row_id) & set(final_test.row_id):
        raise AssertionError("Validation and final-test rows overlap")
    return result


def feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return only features available at the transaction/prediction timestamp."""
    return frame[NUMERIC_FEATURES + CATEGORICAL_FEATURES].copy()


def _storey_midpoint(value: object) -> float:
    numbers = [int(number) for number in re.findall(r"\d+", str(value))]
    return float(sum(numbers) / len(numbers)) if numbers else np.nan


def dataset_checksum(path: Path = DEFAULT_DATASET) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
