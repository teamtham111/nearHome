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

    url = "https://datamall2.mytransport.sg/ltaodataservice/BusStops"
    headers = {"AccountKey": api_key, "accept": "application/json"}
    stops: list[dict] = []
    skip = 0
    while True:
        resp = httpx.get(f"{url}?$skip={skip}", headers=headers, timeout=60)
        resp.raise_for_status()
        batch = resp.json().get("value", [])
        if not batch:
            break
        for row in batch:
            stops.append(
                {
                    "stop_code": row["BusStopCode"],
                    "description": row["Description"],
                    "latitude": float(row["Latitude"]),
                    "longitude": float(row["Longitude"]),
                    "road_name": row.get("RoadName", ""),
                    "services": [],
                }
            )
        skip += 500
        if len(batch) < 500:
            break
    return stops


def attach_services(api_key: str, stops: list[dict], limit: int = 200) -> None:
    import httpx

    headers = {"AccountKey": api_key, "accept": "application/json"}
    for stop in stops[:limit]:
        code = stop["stop_code"]
        url = f"https://datamall2.mytransport.sg/ltaodataservice/BusServices?$filter=BusStopCode eq '{code}'"
        try:
            resp = httpx.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            stop["services"] = sorted({r["ServiceNo"] for r in resp.json().get("value", [])})
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

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(stops, indent=2))
    print(f"Wrote {len(stops)} bus stops to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
