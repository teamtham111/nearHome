#!/usr/bin/env python3
"""Download LTA DataMall BusServices snapshots into fixtures.

BusServices gives one row per (ServiceNo, Direction) with scheduled
dispatch-frequency *ranges* (not exact waits) for AM/PM peak and off-peak
periods. Fetched in bulk with OData pagination.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data_pipeline" / "fixtures" / "lta_bus_services.json"


def fetch_live(api_key: str) -> list[dict]:
    import httpx

    url = "https://datamall2.mytransport.sg/ltaodataservice/BusServices"
    headers = {"AccountKey": api_key, "accept": "application/json"}
    rows: list[dict] = []
    skip = 0
    with httpx.Client(timeout=60) as client:
        while True:
            resp = client.get(url, params={"$skip": skip}, headers=headers)
            resp.raise_for_status()
            batch = resp.json().get("value", [])
            if not batch:
                break
            for row in batch:
                rows.append(
                    {
                        "service_no": row["ServiceNo"],
                        "operator": row.get("Operator", ""),
                        "direction": int(row["Direction"]),
                        "category": row.get("Category", ""),
                        "origin_code": row.get("OriginCode", ""),
                        "destination_code": row.get("DestinationCode", ""),
                        "loop_desc": row.get("LoopDesc", ""),
                        "am_peak_freq": row.get("AM_Peak_Freq", ""),
                        "am_offpeak_freq": row.get("AM_Offpeak_Freq", ""),
                        "pm_peak_freq": row.get("PM_Peak_Freq", ""),
                        "pm_offpeak_freq": row.get("PM_Offpeak_Freq", ""),
                    }
                )
            skip += 500
            if len(batch) < 500:
                break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest LTA BusServices reference data")
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
        print(f"Using existing fixture ({len(rows)} services). Pass --live to refresh from LTA.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2))
    print(f"Wrote {len(rows)} bus service rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
