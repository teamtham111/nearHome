#!/usr/bin/env python3
"""Download the HDB Carpark Information dataset (data.gov.sg) into a fixture.

Used by the Home Parking Convenience component. Static records come from the
official HDB Carpark Information dataset; live lots are fetched separately
from the official data.gov.sg availability API at runtime.

Coordinates in the source dataset are SVY21 (Singapore's local grid); this
script converts them to WGS84 lat/lng using pyproj so the API can compare
them against listing coordinates.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data_pipeline" / "fixtures" / "hdb_carparks.json"
DATASET_ID = "d_23f946fa557947f93a8043bbef41dd09"
SOURCE_NAME = "data.gov.sg HDB Carpark Information"


def _normalise_type(value: str | None) -> str | None:
    if not value:
        return None
    value = " ".join(value.strip().upper().split())
    if "BASEMENT" in value:
        return "BASEMENT"
    if "MULTI-STOREY" in value or "MULTISTOREY" in value:
        return "SURFACE_AND_MULTI_STOREY" if "SURFACE" in value else "MULTI_STOREY"
    if "SURFACE" in value:
        return "SURFACE"
    return "OTHER"


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def fetch_live() -> list[dict]:
    import httpx
    from pyproj import Transformer

    # EPSG:3414 = SVY21 (Singapore), EPSG:4326 = WGS84 lat/lng.
    transformer = Transformer.from_crs("EPSG:3414", "EPSG:4326", always_xy=True)

    url = "https://data.gov.sg/api/action/datastore_search"
    rows: list[dict] = []
    offset = 0
    limit = 500
    with httpx.Client(timeout=30) as client:
        while True:
            for attempt in range(5):
                resp = client.get(url, params={"resource_id": DATASET_ID, "limit": limit, "offset": offset})
                if resp.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                resp.raise_for_status()
                break
            else:
                raise RuntimeError("data.gov.sg rate-limited after 5 retries")
            payload = resp.json()
            records = payload.get("result", {}).get("records", [])
            if not records:
                break
            for r in records:
                try:
                    x, y = float(r["x_coord"]), float(r["y_coord"])
                    lng, lat = transformer.transform(x, y)
                except (KeyError, ValueError, TypeError):
                    continue
                source_type = r.get("car_park_type")
                rows.append(
                    {
                        "carpark_no": r.get("car_park_no", ""),
                        "address": r.get("address", ""),
                        "latitude": round(lat, 6),
                        "longitude": round(lng, 6),
                        "carpark_type": _normalise_type(source_type),
                        "source_carpark_type": source_type,
                        "parking_system_type": _optional_text(r.get("type_of_parking_system")),
                        "short_term_parking": _optional_text(r.get("short_term_parking")),
                        "free_parking": _optional_text(r.get("free_parking")),
                        "night_parking": _optional_text(r.get("night_parking")),
                        "carpark_decks": r.get("car_park_decks") or None,
                        "gantry_height_m": _optional_float(r.get("gantry_height")),
                        "basement_indicator": _optional_text(r.get("car_park_basement")),
                        "source": SOURCE_NAME,
                        "source_updated_at": datetime.now(UTC).isoformat(),
                    }
                )
            offset += limit
            if len(records) < limit:
                break
            time.sleep(1.0)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest HDB Carpark Information (data.gov.sg)")
    parser.add_argument("--live", action="store_true", help="Fetch from data.gov.sg")
    parser.add_argument(
        "--persist-db",
        action="store_true",
        help="Mirror the refreshed fixture into the API database after writing it",
    )
    parser.add_argument("--output", type=Path, default=FIXTURES)
    args = parser.parse_args()

    if args.live:
        rows = fetch_live()
    else:
        rows = json.loads(FIXTURES.read_text()) if FIXTURES.exists() else []
        print(f"Using existing fixture ({len(rows)} carparks). Pass --live to refresh.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2))
    print(f"Wrote {len(rows)} HDB carparks to {args.output}")
    if args.persist_db:
        sys.path.insert(0, str(ROOT / "apps" / "api"))
        from app.adapters.parking.hdb_carpark import HdbCarparkStore  # noqa: E402
        from app.db.session import SessionLocal  # noqa: E402
        from app.repositories.carpark_repository import CarparkRepository  # noqa: E402

        HdbCarparkStore.reset_cache()
        with SessionLocal() as db:
            CarparkRepository(db).replace_static_records(HdbCarparkStore.load())
        print("Mirrored official HDB carparks into the API database")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
