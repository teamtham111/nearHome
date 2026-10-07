#!/usr/bin/env python3
"""Download LTA DataMall bus stop snapshots into fixtures (optional live fetch)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data_pipeline" / "fixtures" / "lta_bus_stops.json"


def fetch_live(api_key: str) -> list[dict]:
    import httpx

    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from app.adapters.provider_schemas.reference import LtaDataMallBusStopsResponse

    url = "https://datamall2.mytransport.sg/ltaodataservice/BusStops"
    headers = {"AccountKey": api_key, "accept": "application/json"}
    stops: list[dict] = []
    skip = 0
    while True:
        resp = httpx.get(f"{url}?$skip={skip}", headers=headers, timeout=60)
        resp.raise_for_status()
        batch = LtaDataMallBusStopsResponse.model_validate(resp.json()).value
        if not batch:
            break
        for row in batch:
            stops.append(
                {
                    "stop_code": row.stop_code,
                    "description": row.description,
                    "latitude": row.latitude,
                    "longitude": row.longitude,
                    "road_name": row.road_name,
                    "services": [],
                }
            )
        skip += 500
        if len(batch) < 500:
            break
    return stops


def attach_services(api_key: str, stops: list[dict], limit: int = 200) -> None:
    import httpx

    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from app.adapters.provider_schemas.reference import LtaDataMallServicesAtStopResponse

    headers = {"AccountKey": api_key, "accept": "application/json"}
    for stop in stops[:limit]:
        code = stop["stop_code"]
        url = f"https://datamall2.mytransport.sg/ltaodataservice/BusServices?$filter=BusStopCode eq '{code}'"
        try:
            resp = httpx.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            response = LtaDataMallServicesAtStopResponse.model_validate(resp.json())
            stop["services"] = sorted({row.service_no for row in response.value})
        except Exception:
            stop["services"] = stop.get("services", [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest LTA bus stop reference data")
    parser.add_argument("--live", action="store_true", help="Fetch from LTA DataMall API")
    parser.add_argument("--with-services", action="store_true", help="Attach bus services (slow)")
    parser.add_argument("--output", type=Path, default=FIXTURES)
    args = parser.parse_args()

    if args.live:
        api_key = os.environ.get("LTA_ACCOUNT_KEY", "")
        if not api_key:
            print("Set LTA_ACCOUNT_KEY for live fetch", file=sys.stderr)
            return 1
        stops = fetch_live(api_key)
        if args.with_services:
            attach_services(api_key, stops)
    else:
        stops = json.loads(FIXTURES.read_text()) if FIXTURES.exists() else []
        print(f"Using existing fixture ({len(stops)} stops). Pass --live to refresh from LTA.")

    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from pydantic import TypeAdapter

    from app.adapters.provider_schemas.reference import LtaBusStopSnapshotRow

    validated_stops = TypeAdapter(list[LtaBusStopSnapshotRow]).validate_python(stops)
    serialized_stops = [stop.model_dump(mode="json") for stop in validated_stops]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(serialized_stops, indent=2))
    print(f"Wrote {len(serialized_stops)} bus stops to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
