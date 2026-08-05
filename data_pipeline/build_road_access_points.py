#!/usr/bin/env python3
"""Compile a curated, direction-labelled set of major-road access points.

Replaces the previous 5 hardcoded lat/lng anchors. Each entry names a real
arterial road that feeds a specific expressway, in a specific travel
direction — e.g. "Tampines Ave 10 -> PIE (towards Changi/eastbound)". These
are geocoded live via OneMap (the road/junction name itself, not a
fabricated slip-road coordinate) so every point resolves to a verifiable
real location.

This is a best-effort curated reference (not a live feed) covering all 10
major Singapore expressways (PIE, CTE, ECP, AYE, BKE, KJE, KPE, MCE, SLE,
TPE) plus arterial equivalents, documented with a compilation date. The
driving engine picks the *useful* point per listing by ROUTED duration
across several nearby candidates — this file only supplies the candidate
set, never the final answer.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

OUTPUT = ROOT / "data_pipeline" / "fixtures" / "road_access_points.json"
COMPILED_VERSION = "2026-08-02"
SOURCE_NOTE = (
    "Manually curated list of named arterial roads that feed each major "
    "Singapore expressway, geocoded live via OneMap. Not a live feed — "
    "revalidate against LTA's expressway network if road names change."
)

# (name, expressway, direction_label, geocode_query)
ACCESS_POINTS: list[tuple[str, str, str, str]] = [
    ("PIE via Jurong West St 41", "PIE", "westbound towards Tuas/Jurong", "Jurong West Street 41"),
    ("PIE via Clementi Ave 6", "PIE", "westbound towards Clementi/Jurong", "Clementi Avenue 6"),
    ("PIE via Braddell Rd", "PIE", "eastbound/westbound via central Singapore", "Braddell Road"),
    ("PIE via Tampines Ave 10", "PIE", "eastbound towards Changi/Tampines", "Tampines Avenue 10"),
    ("PIE via Loyang Ave", "PIE", "eastbound towards Changi Airport", "Loyang Avenue"),
    ("CTE via Ang Mo Kio Ave 1", "CTE", "northbound towards Yishun/Woodlands", "Ang Mo Kio Avenue 1"),
    ("CTE via Braddell Rd", "CTE", "citybound/northbound via Toa Payoh", "Braddell Road"),
    ("CTE via Moulmein Rd", "CTE", "citybound towards city centre", "Moulmein Road"),
    ("CTE via Kim Seng Rd", "CTE", "citybound, southern terminus area", "Kim Seng Road"),
    ("ECP via Bedok South Ave 1", "ECP", "eastbound towards Changi", "Bedok South Avenue 1"),
    ("ECP via Marine Parade Rd", "ECP", "citybound towards Marina/city centre", "Marine Parade Road"),
    ("ECP via Changi Coast Rd", "ECP", "eastbound towards Changi Airport", "Changi Coast Road"),
    ("AYE via Alexandra Rd", "AYE", "citybound towards city centre", "Alexandra Road"),
    ("AYE via Jurong Gateway Rd", "AYE", "westbound towards Jurong/Tuas", "Jurong Gateway Road"),
    ("AYE via Tuas South Ave 1", "AYE", "westbound terminus towards Tuas", "Tuas South Avenue 1"),
    ("BKE via Dairy Farm Rd", "BKE", "northbound towards Woodlands/Mandai", "Dairy Farm Road"),
    ("BKE via Petir Rd", "BKE", "northbound towards Woodlands", "Petir Road"),
    ("KJE via Choa Chu Kang Ave 4", "KJE", "westbound towards Choa Chu Kang", "Choa Chu Kang Avenue 4"),
    ("KJE via Woodlands Rd", "KJE", "northbound towards Woodlands/BKE", "Woodlands Road"),
    ("KPE via Ubi Ave 3", "KPE", "southbound towards MCE/city", "Ubi Avenue 3"),
    ("KPE via Serangoon Ave 3", "KPE", "central section via Serangoon", "Serangoon Avenue 3"),
    ("KPE via Punggol Field", "KPE", "northbound towards Punggol/TPE", "Punggol Field"),
    ("MCE via Marina Coastal Dr", "MCE", "connects ECP/AYE via Marina South", "Marina Coastal Drive"),
    ("MCE via Shenton Way", "MCE", "city-centre southern access", "Shenton Way"),
    ("SLE via Yishun Ave 2", "SLE", "westbound towards BKE/Mandai", "Yishun Avenue 2"),
    ("SLE via Ang Mo Kio Ave 5", "SLE", "eastbound towards TPE/Seletar", "Ang Mo Kio Avenue 5"),
    ("TPE via Pasir Ris Dr 3", "TPE", "eastbound towards Pasir Ris/Changi", "Pasir Ris Drive 3"),
    ("TPE via Hougang Ave 8", "TPE", "central section via Hougang", "Hougang Avenue 8"),
    ("TPE via Punggol Way", "TPE", "northbound towards Punggol/KPE", "Punggol Way"),
    ("PIE via Adam Rd", "PIE", "central section via Adam/Farrer", "Adam Road"),
]


def main() -> int:
    from app.adapters.live.onemap import LiveOneMapAdapter

    adapter = LiveOneMapAdapter()
    points = []
    failures = []
    for idx, (name, expressway, direction, query) in enumerate(ACCESS_POINTS):
        try:
            result = adapter.geocode(query)
            points.append(
                {
                    "name": name,
                    "expressway": expressway,
                    "direction_label": direction,
                    "latitude": result.latitude,
                    "longitude": result.longitude,
                    "geocode_query": query,
                }
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc}")
        time.sleep(0.1)
        if idx % 10 == 0:
            print(f"  geocoded {idx}/{len(ACCESS_POINTS)}...")

    if failures:
        print(f"WARNING: {len(failures)} access points failed to geocode:")
        for f in failures:
            print(f"  - {f}")

    OUTPUT.write_text(
        json.dumps({"source": SOURCE_NOTE, "version": COMPILED_VERSION, "access_points": points}, indent=2)
    )
    print(f"Wrote {len(points)} road access points to {OUTPUT}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
