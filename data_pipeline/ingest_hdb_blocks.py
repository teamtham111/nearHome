#!/usr/bin/env python3
"""Build the HDB residential block reference registry used for location benchmarks.

Source: HDB Property Information (data.gov.sg, Housing & Development Board).
Every row is one HDB building with its block number, street, residential flag
and HDB building-contract town code. The town legend is read from the
dataset's own published column metadata, so town membership comes from HDB
rather than from a NearHome boundary approximation.

The dataset has no coordinates. Each residential block is geocoded once with
OneMap (SLA) and accepted only when OneMap returns the same block number on an
equivalent street. Earlier geocodes for unchanged blocks are reused, so a
refresh only geocodes new or changed blocks. Unmatched blocks remain in the
registry without coordinates and are reported as coverage gaps; they are
never assigned an approximate location.

Usage:
  apps/api/.venv/bin/python data_pipeline/ingest_hdb_blocks.py --live
  apps/api/.venv/bin/python data_pipeline/ingest_hdb_blocks.py --csv path/to.csv --persist-db
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from data_pipeline.data_gov_client import DataGovClient, write_json_atomic  # noqa: E402

DATASET_ID = "d_17f5382f26140b1fdae0ba2ef6239d2f"
METADATA_URL = f"https://api-production.data.gov.sg/v2/public/api/datasets/{DATASET_ID}/metadata"
SOURCE_NAME = "data.gov.sg HDB Property Information"
RAW_CSV = ROOT / "data_pipeline" / "snapshots" / "raw" / "hdb_property_information.csv"
FIXTURE = ROOT / "data_pipeline" / "fixtures" / "hdb_blocks.json"
TRANSACTIONS = ROOT / "data_pipeline" / "fixtures" / "hdb_transactions.json"
REGISTRY_VERSION = "hdb-blocks-v1"
ONEMAP_SEARCH = "https://www.onemap.gov.sg/api/common/elastic/search"
# OneMap documents 250 requests/minute; stay comfortably below it.
ONEMAP_MIN_INTERVAL_SECONDS = 0.27


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fetch_town_legend() -> dict[str, str]:
    """Parse ``CODE - NAME`` pairs from HDB's published column description."""
    import httpx

    response = httpx.get(METADATA_URL, timeout=30.0)
    response.raise_for_status()
    columns = response.json()["data"]["columnMetadata"]["metaMapping"]
    description = next(
        column.get("description", "") for column in columns.values() if column.get("name") == "bldg_contract_town"
    )
    return parse_town_legend(description)


def parse_town_legend(description: str) -> dict[str, str]:
    text = description.split(":", 1)[-1]
    pairs = re.findall(r"([A-Z]{2,3})\s*-\s*([A-Z/ ]+?)(?=\s+[A-Z]{2,3}\s*-|$)", text.strip())
    legend = {code: name.strip() for code, name in pairs}
    if len(legend) < 20:
        raise RuntimeError("HDB town legend could not be parsed from dataset metadata")
    return legend


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [{key.strip(): (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]


def _onemap_token(client) -> str:
    response = client.post(
        "https://www.onemap.gov.sg/api/auth/post/getToken",
        json={"email": os.environ["ONEMAP_EMAIL"], "password": os.environ["ONEMAP_PASSWORD"]},
    )
    response.raise_for_status()
    return response.json()["access_token"]


class OneMapBlockGeocoder:
    def __init__(self) -> None:
        import httpx

        self._client = httpx.Client(timeout=20.0)
        self._token = _onemap_token(self._client)
        self._last = 0.0

    def close(self) -> None:
        self._client.close()

    def _search(self, query: str) -> list[dict]:
        for attempt in range(6):
            wait = ONEMAP_MIN_INTERVAL_SECONDS - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self._client.get(
                    ONEMAP_SEARCH,
                    params={"searchVal": query, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1},
                    headers={"Authorization": self._token},
                )
            except Exception:
                time.sleep(2**attempt)
                continue
            if response.status_code == 401:
                self._token = _onemap_token(self._client)
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            response.raise_for_status()
            return response.json().get("results", [])
        raise RuntimeError("OneMap search kept failing")

    def geocode(self, block: str, street: str) -> dict:
        from app.adapters.provider_schemas.onemap import OneMapSearchResult
        from app.utils.hdb_address import canonical_hdb_parts
        from app.utils.hdb_block_identity import streets_equivalent

        requested = canonical_hdb_parts(block, street)
        try:
            results = self._search(f"{block} {street}")
        except RuntimeError:
            return {"geocode_status": "provider_error"}
        for raw in results:
            try:
                row = OneMapSearchResult.model_validate(raw)
            except Exception:
                continue
            returned = canonical_hdb_parts(row.block, row.road_name)
            if not requested or not returned or returned[0] != requested[0]:
                continue
            exact = returned[1] == requested[1]
            if not exact and not streets_equivalent(requested[1], returned[1]):
                continue
            if not (1.15 <= row.latitude <= 1.49 and 103.58 <= row.longitude <= 104.12):
                continue
            return {
                "geocode_status": "matched",
                "geocode_match": "exact" if exact else "abbreviation",
                "latitude": row.latitude,
                "longitude": row.longitude,
                "postal_code": row.postal_code if row.postal_code and row.postal_code.isdigit() else None,
                "onemap_block": row.block,
                "onemap_road_name": row.road_name,
                "onemap_address": row.address,
            }
        return {"geocode_status": "unmatched"}


def build_registry(
    rows: list[dict[str, str]],
    legend: dict[str, str],
    previous: dict[str, dict],
    geocoder: OneMapBlockGeocoder | None,
    *,
    checkpoint=None,
) -> list[dict]:
    from app.utils.hdb_block_identity import block_key_from_parts

    blocks: dict[str, dict] = {}
    for row in rows:
        key = block_key_from_parts(row.get("blk_no"), row.get("street"))
        if key is None:
            continue
        town_code = row.get("bldg_contract_town") or None
        record = {
            "block_key": key,
            "block": row.get("blk_no"),
            "street": row.get("street"),
            "residential": row.get("residential") == "Y",
            "town_code": town_code,
            "town_name": legend.get(town_code or ""),
            "year_completed": int(row["year_completed"]) if row.get("year_completed", "").isdigit() else None,
            "max_floor_level": int(row["max_floor_lvl"]) if row.get("max_floor_lvl", "").isdigit() else None,
            "total_dwelling_units": (
                int(row["total_dwelling_units"]) if row.get("total_dwelling_units", "").isdigit() else None
            ),
        }
        # A duplicate key would mean two buildings collapse into one identity.
        if key in blocks:
            blocks[key]["duplicate_source_rows"] = blocks[key].get("duplicate_source_rows", 1) + 1
            continue
        blocks[key] = record

    pending = 0
    for index, (key, record) in enumerate(sorted(blocks.items())):
        if not record["residential"]:
            continue
        prior = previous.get(key)
        if prior and prior.get("geocode_status") == "matched" and prior.get("street") == record["street"]:
            record.update({field: prior.get(field) for field in _GEO_FIELDS})
            continue
        if geocoder is None:
            record["geocode_status"] = "not_geocoded"
            continue
        record.update(geocoder.geocode(record["block"], record["street"]))
        record["geocoded_at"] = datetime.now(UTC).isoformat()
        pending += 1
        if checkpoint and pending % 250 == 0:
            checkpoint(list(blocks.values()))
            print(f"geocoded {pending} new blocks (row {index})", flush=True)
    return list(blocks.values())


_GEO_FIELDS = (
    "geocode_status",
    "geocode_match",
    "latitude",
    "longitude",
    "postal_code",
    "onemap_block",
    "onemap_road_name",
    "onemap_address",
    "geocoded_at",
)


def town_crosscheck(blocks: list[dict]) -> dict[str, object]:
    """Compare HDB town codes with town names on resale transactions for the same block."""
    if not TRANSACTIONS.exists():
        return {"status": "skipped", "reason": "transactions fixture missing"}
    from app.utils.hdb_block_identity import block_key_from_parts

    by_key: dict[str, Counter] = defaultdict(Counter)
    for tx in json.loads(TRANSACTIONS.read_text()):
        key = block_key_from_parts(tx.get("block"), tx.get("street"))
        if key:
            by_key[key][tx.get("town")] += 1
    agree = disagree = 0
    examples: list[dict[str, object]] = []
    for block in blocks:
        towns = by_key.get(block["block_key"])
        if not towns or not block.get("town_name"):
            continue
        tx_town = towns.most_common(1)[0][0]
        if tx_town == block["town_name"]:
            agree += 1
        else:
            disagree += 1
            if len(examples) < 20:
                examples.append({"block_key": block["block_key"], "hdb_town": block["town_name"], "tx_town": tx_town})
    return {"status": "checked", "agree": agree, "disagree": disagree, "examples": examples}


def write_fixture(
    blocks: list[dict], legend: dict[str, str], *, retrieved_at: str, source_updated_at: str | None
) -> None:
    residential = [b for b in blocks if b["residential"]]
    matched = [b for b in residential if b.get("geocode_status") == "matched"]
    payload = {
        "metadata": {
            "registry_version": REGISTRY_VERSION,
            "source": SOURCE_NAME,
            "dataset_id": DATASET_ID,
            "source_updated_at": source_updated_at,
            "retrieved_at": retrieved_at,
            "town_source": "HDB Property Information bldg_contract_town with HDB's published column legend",
            "town_legend": legend,
            "row_count": len(blocks),
            "residential_block_count": len(residential),
            "geocoded_residential_block_count": len(matched),
            "geocode_status_counts": dict(Counter(b.get("geocode_status") or "pending" for b in residential)),
            "unmapped_town_codes": sorted({b["town_code"] for b in blocks if b["town_code"] and not b["town_name"]}),
        },
        "blocks": sorted(blocks, key=lambda b: b["block_key"]),
    }
    write_json_atomic(FIXTURE, payload)


def persist_db(blocks: list[dict], metadata: dict) -> int:
    from app.db.session import SessionLocal
    from app.repositories.block_repository import BlockRepository

    with SessionLocal() as db:
        count = BlockRepository(db).upsert_registry(blocks, metadata)
        db.commit()
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="Download the latest official dataset first")
    parser.add_argument("--csv", type=Path, default=RAW_CSV)
    parser.add_argument("--no-geocode", action="store_true", help="Skip OneMap; keep earlier geocodes only")
    parser.add_argument("--persist-db", action="store_true", help="Mirror the registry into PostgreSQL")
    args = parser.parse_args()
    _load_env()

    if args.live:
        DataGovClient().download(DATASET_ID, args.csv, initiate=True, expected_suffix=".csv")
    rows = read_rows(args.csv)
    legend = fetch_town_legend()
    source_updated_at = None
    try:
        import httpx

        source_updated_at = httpx.get(METADATA_URL, timeout=30.0).json()["data"].get("lastUpdatedAt")
    except Exception:
        pass
    previous: dict[str, dict] = {}
    if FIXTURE.exists():
        previous = {b["block_key"]: b for b in json.loads(FIXTURE.read_text()).get("blocks", [])}

    retrieved_at = datetime.now(UTC).isoformat()
    geocoder = None if args.no_geocode else OneMapBlockGeocoder()
    try:
        blocks = build_registry(
            rows,
            legend,
            previous,
            geocoder,
            checkpoint=lambda partial: write_fixture(
                partial, legend, retrieved_at=retrieved_at, source_updated_at=source_updated_at
            ),
        )
    finally:
        if geocoder:
            geocoder.close()
    write_fixture(blocks, legend, retrieved_at=retrieved_at, source_updated_at=source_updated_at)
    payload = json.loads(FIXTURE.read_text())
    payload["metadata"]["town_crosscheck"] = town_crosscheck(blocks)
    write_json_atomic(FIXTURE, payload)
    if args.persist_db:
        payload["metadata"]["persisted_rows"] = persist_db(payload["blocks"], payload["metadata"])
    print(json.dumps(payload["metadata"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
