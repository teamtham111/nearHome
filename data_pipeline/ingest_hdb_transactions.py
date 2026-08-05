#!/usr/bin/env python3
"""Ingest HDB resale transaction CSV into fixture JSON (demo pipeline)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "data_pipeline" / "fixtures" / "hdb_transactions.json"
SNAPSHOT_DIR = ROOT / "data_pipeline" / "snapshots"


def parse_remaining_lease_months(value: object) -> int | None:
    """Parse data.gov.sg values like '61 years 04 months' into months.

    Missing or malformed source values remain missing. They must never become
    a fabricated lease estimate.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        months = round(float(value) * 12)
        return months if 0 <= months <= 99 * 12 else None
    text = str(value).lower().strip()
    years_match = re.search(r"(\d+)\s*years?", text)
    months_match = re.search(r"(\d+)\s*months?", text)
    years = int(years_match.group(1)) if years_match else 0
    months = int(months_match.group(1)) if months_match else 0
    if years == 0 and months == 0:
        return None
    total = years * 12 + months
    return total if 0 <= total <= 99 * 12 else None


def parse_remaining_lease(value: object) -> float | None:
    """Compatibility wrapper returning years for legacy fixture consumers."""
    months = parse_remaining_lease_months(value)
    return round(months / 12.0, 2) if months is not None else None


def ingest(csv_path: Path, output: Path | None = None, min_month: str | None = "2022-01") -> dict:
    df = pd.read_csv(csv_path)
    df = df.rename(columns={c: c.lower() for c in df.columns})

    if min_month and "month" in df.columns:
        df = df[df["month"].astype(str) >= min_month]

    records = []
    for idx, row in df.iterrows():
        area = float(row.get("floor_area_sqm", row.get("floor_area", 90)))
        price = float(row.get("resale_price", 0))
        if price <= 0 or area <= 0:
            continue
        lease_raw = row.get("lease_commence_date", row.get("lease_commencement", 1990))
        try:
            lease_year = int(str(lease_raw)[:4])
        except (TypeError, ValueError):
            lease_year = 1990
        records.append(
            {
                "transaction_id": f"ingest-{idx}",
                "transaction_month": str(row.get("month", ""))[:7],
                "town": str(row.get("town", "")).upper(),
                "flat_type": str(row.get("flat_type", "")).upper(),
                "block": str(row.get("block", "")),
                "street": str(row.get("street_name", row.get("street", ""))).upper(),
                "storey_range": str(row.get("storey_range", "")),
                "floor_area_sqm": area,
                "flat_model": str(row.get("flat_model", "")) or None,
                "lease_commencement": lease_year,
                "remaining_lease": parse_remaining_lease(row.get("remaining_lease")),
                "remaining_lease_months": parse_remaining_lease_months(row.get("remaining_lease")),
                "resale_price": price,
                "price_per_sqm": round(price / area, 2),
            }
        )

    out_path = output or FIXTURE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(records, indent=2))

    checksum = hashlib.sha256(out_path.read_bytes()).hexdigest()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "dataset_id": csv_path.name,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "row_count": len(records),
        "checksum": checksum,
        "output": str(out_path),
    }
    meta_path = SNAPSHOT_DIR / f"{csv_path.stem}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest HDB resale CSV to NearHome fixture")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument(
        "--min-month",
        type=str,
        default="2022-01",
        help="Only keep transactions from this month onward (YYYY-MM). Use '' for all rows.",
    )
    args = parser.parse_args()
    min_month = args.min_month or None
    result = ingest(args.csv_path, args.output, min_month=min_month)
    print(json.dumps(result, indent=2))
