#!/usr/bin/env python3
"""Download LTA DataMall BusRoutes snapshots into fixtures.

BusRoutes gives the ordered stop sequence for every (ServiceNo, Direction)
pair — this is what lets us know which physical bus stops a given service
actually visits, in what order, preserving direction. Fetched in bulk with
OData pagination (never one request per stop, per the transport-data spec).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data_pipeline" / "fixtures" / "lta_bus_routes.json"


def fetch_live(api_key: str) -> list[dict]:
    import httpx

    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from app.adapters.provider_schemas.reference import LtaDataMallBusRoutesResponse

    url = "https://datamall2.mytransport.sg/ltaodataservice/BusRoutes"
    headers = {"AccountKey": api_key, "accept": "application/json"}
    rows: list[dict] = []
    skip = 0
    with httpx.Client(timeout=60) as client:
        while True:
            resp = client.get(url, params={"$skip": skip}, headers=headers)
            resp.raise_for_status()
            batch = LtaDataMallBusRoutesResponse.model_validate(resp.json()).value
            if not batch:
                break
            for row in batch:
                rows.append(
                    {
                        "service_no": row.service_no,
                        "operator": row.operator,
                        "direction": row.direction,
                        "stop_sequence": row.stop_sequence,
                        "bus_stop_code": row.bus_stop_code,
                        "distance_km": row.distance_km,
                        "wd_first_bus": row.wd_first_bus,
                        "wd_last_bus": row.wd_last_bus,
                        "sat_first_bus": row.sat_first_bus,
                        "sat_last_bus": row.sat_last_bus,
                        "sun_first_bus": row.sun_first_bus,
                        "sun_last_bus": row.sun_last_bus,
                    }
                )
            skip += 500
            if len(batch) < 500:
                break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest LTA BusRoutes reference data")
    parser.add_argument("--live", action="store_true", help="Fetch from LTA DataMall API")
    parser.add_argument("--output", type=Path, default=FIXTURES)
    args = parser.parse_args()

    if args.live:
        api_key = os.environ.get("LTA_ACCOUNT_KEY", "")
        if not api_key:
            print("Set LTA_ACCOUNT_KEY for live fetch", file=sys.stderr)
            return 1
        rows = fetch_live(api_key)
    else:
        rows = json.loads(FIXTURES.read_text()) if FIXTURES.exists() else []
        print(f"Using existing fixture ({len(rows)} route-stops). Pass --live to refresh from LTA.")

    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from pydantic import TypeAdapter

    from app.adapters.provider_schemas.reference import LtaBusRouteSnapshotRow

    validated_rows = TypeAdapter(list[LtaBusRouteSnapshotRow]).validate_python(rows)
    serialized_rows = [row.model_dump(mode="json") for row in validated_rows]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(serialized_rows, indent=2))
    print(f"Wrote {len(serialized_rows)} bus route-stop rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
