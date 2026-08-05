#!/usr/bin/env python3
"""Build and report on the joined bus indexes (services_by_stop, etc.).

The actual join logic lives in `app.adapters.transport_data.lta_bus` so the
runtime engines and this CLI report share exactly one implementation. This
script just triggers the load and writes a human-readable data-quality
report — it does not need to write a separate index file, since the join is
cheap enough (tens of thousands of rows) to build in-memory at API startup.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.adapters.transport_data.lta_bus import LtaBusDataStore  # noqa: E402

REPORT_PATH = ROOT / "data_pipeline" / "fixtures" / "bus_data_quality_report.json"


def main() -> int:
    LtaBusDataStore.reset_cache()
    report = LtaBusDataStore.quality_report()
    REPORT_PATH.write_text(json.dumps(report.to_dict(), indent=2))

    print(f"Bus stops:                {report.bus_stops_count}")
    print(f"BusRoutes rows:            {report.bus_routes_rows}")
    print(f"BusServices rows:          {report.bus_services_rows}")
    print(f"Service-directions (routes):   {report.unique_service_directions_in_routes}")
    print(f"Service-directions (services): {report.unique_service_directions_in_services}")
    print(f"Stops with no matched route:   {report.stops_with_no_routes}")
    print(f"Unknown stop-code references:  {report.route_stops_referencing_unknown_stop_codes}")
    print(f"Duplicate stop sequences:      {report.duplicate_stop_sequences}")
    print(f"Malformed frequency values:    {report.malformed_frequency_values}")
    print(f"Usable: {report.is_usable}")
    if report.problems:
        print("Problems:")
        for p in report.problems:
            print(f"  - {p}")
    print(f"Report written to {REPORT_PATH}")
    return 0 if report.is_usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
