#!/usr/bin/env python3
"""Compile the curated MRT/LRT structural rail dataset into the graph fixtures.

WHY THIS FILE EXISTS
---------------------
LTA DataMall has no public station-adjacency/topology API — BusRoutes and
BusServices (ingested elsewhere in this pipeline) are bus-only. There is no
equivalent "RailRoutes" dataset. So the *structure* below (which stations
exist, which line, what order, which stations are the same physical
interchange) is a hand-compiled reference, cross-checked against LTA's own
published station list (via Wikipedia's "List of Singapore MRT/LRT stations"
articles, which cite LTA's official System Map and are kept in sync with it)
as of the date in RAIL_DATA_VERSION below. It is NOT a live feed and must be
periodically revalidated against LTA's official system map if the network
changes (new lines/stations open).

Coordinates are NOT hand-typed — this script geocodes every station name
live via the OneMap adapter (already configured for this project), so
coordinates come from an authoritative source rather than guesswork.

Only stations listed as "In operation" (not planned/under construction/
non-operational) are included, per the transport-and-driving spec's
"opening/active status" requirement.

Output:
  data_pipeline/fixtures/rail/rail_lines_structure.csv  (source structure)
  data_pipeline/fixtures/rail/rail_stations.json        (geocoded nodes)
  data_pipeline/fixtures/rail/rail_edges.csv            (ride + transfer edges)
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

RAIL_DATA_VERSION = "2026-08-02"
RAIL_DATA_SOURCE = (
    "Manually compiled from LTA's official System Map (via Wikipedia's "
    "'List of Singapore MRT stations' / 'List of Singapore LRT stations', "
    "which cite the LTA System Map) — not a live feed. Revalidate against "
    "LTA's published system map if the network has changed since "
    f"{RAIL_DATA_VERSION}."
)

FIXTURES_DIR = ROOT / "data_pipeline" / "fixtures" / "rail"
STRUCTURE_CSV = FIXTURES_DIR / "rail_lines_structure.csv"
STATIONS_JSON = FIXTURES_DIR / "rail_stations.json"
EDGES_CSV = FIXTURES_DIR / "rail_edges.csv"

# ---------------------------------------------------------------------------
# 1. Physical stations: name -> list of station-line codes present there.
#    A dash between codes in the source table denotes a "tap-out transfer"
#    (you must exit and re-enter the paid area) — tracked in TAP_OUT_PAIRS.
# ---------------------------------------------------------------------------
STATIONS: dict[str, list[str]] = {
    # North-South Line
    "Jurong East": ["NS1", "EW24"], "Bukit Batok": ["NS2"], "Bukit Gombak": ["NS3"],
    "Choa Chu Kang": ["NS4", "BP1"], "Yew Tee": ["NS5"], "Kranji": ["NS7"],
    "Marsiling": ["NS8"], "Woodlands": ["NS9", "TE2"], "Admiralty": ["NS10"],
    "Sembawang": ["NS11"], "Canberra": ["NS12"], "Yishun": ["NS13"], "Khatib": ["NS14"],
    "Yio Chu Kang": ["NS15"], "Ang Mo Kio": ["NS16"], "Bishan": ["NS17", "CC15"],
    "Braddell": ["NS18"], "Toa Payoh": ["NS19"], "Novena": ["NS20"],
    "Newton": ["NS21", "DT11"], "Orchard": ["NS22", "TE14"], "Somerset": ["NS23"],
    "Dhoby Ghaut": ["NS24", "NE6", "CC1"], "City Hall": ["NS25", "EW13"],
    "Raffles Place": ["NS26", "EW14"], "Marina Bay": ["NS27", "CC33", "TE20"],
    "Marina South Pier": ["NS28"],
    # East-West Line (+ Changi Airport branch)
    "Pasir Ris": ["EW1"], "Tampines": ["EW2", "DT32"], "Simei": ["EW3"],
    "Tanah Merah": ["EW4"], "Bedok": ["EW5"], "Kembangan": ["EW6"], "Eunos": ["EW7"],
    "Paya Lebar": ["EW8", "CC9"], "Aljunied": ["EW9"], "Kallang": ["EW10"],
    "Lavender": ["EW11"], "Bugis": ["EW12", "DT14"], "Tanjong Pagar": ["EW15"],
    "Outram Park": ["EW16", "NE3", "TE17"], "Tiong Bahru": ["EW17"], "Redhill": ["EW18"],
    "Queenstown": ["EW19"], "Commonwealth": ["EW20"], "Buona Vista": ["EW21", "CC22"],
    "Dover": ["EW22"], "Clementi": ["EW23"], "Chinese Garden": ["EW25"],
    "Lakeside": ["EW26"], "Boon Lay": ["EW27"], "Pioneer": ["EW28"], "Joo Koon": ["EW29"],
    "Gul Circle": ["EW30"], "Tuas Crescent": ["EW31"], "Tuas West Road": ["EW32"],
    "Tuas Link": ["EW33"], "Expo": ["CG1", "DT35"], "Changi Airport": ["CG2"],
    # North East Line
    "HarbourFront": ["NE1", "CC29"], "Chinatown": ["NE4", "DT19"], "Clarke Quay": ["NE5"],
    "Little India": ["NE7", "DT12"], "Farrer Park": ["NE8"], "Boon Keng": ["NE9"],
    "Potong Pasir": ["NE10"], "Woodleigh": ["NE11"], "Serangoon": ["NE12", "CC13"],
    "Kovan": ["NE13"], "Hougang": ["NE14"], "Buangkok": ["NE15"],
    "Sengkang": ["NE16", "STC"], "Punggol": ["NE17", "PTC"], "Punggol Coast": ["NE18"],
    # Circle Line
    "Bras Basah": ["CC2"], "Esplanade": ["CC3"], "Promenade": ["CC4", "DT15"],
    "Nicoll Highway": ["CC5"], "Stadium": ["CC6"], "Mountbatten": ["CC7"],
    "Dakota": ["CC8"], "MacPherson": ["CC10", "DT26"], "Tai Seng": ["CC11"],
    "Bartley": ["CC12"], "Lorong Chuan": ["CC14"], "Marymount": ["CC16"],
    "Caldecott": ["CC17", "TE9"], "Botanic Gardens": ["CC19", "DT9"],
    "Farrer Road": ["CC20"], "Holland Village": ["CC21"], "one-north": ["CC23"],
    "Kent Ridge": ["CC24"], "Haw Par Villa": ["CC25"], "Pasir Panjang": ["CC26"],
    "Labrador Park": ["CC27"], "Telok Blangah": ["CC28"], "Keppel": ["CC30"],
    "Cantonment": ["CC31"], "Prince Edward Road": ["CC32"], "Bayfront": ["CC34", "DT16"],
    # Downtown Line
    "Bukit Panjang": ["BP6", "DT1"], "Cashew": ["DT2"], "Hillview": ["DT3"],
    "Hume": ["DT4"], "Beauty World": ["DT5"], "King Albert Park": ["DT6"],
    "Sixth Avenue": ["DT7"], "Tan Kah Kee": ["DT8"], "Rochor": ["DT13"],
    "Downtown": ["DT17"], "Telok Ayer": ["DT18"], "Fort Canning": ["DT20"],
    "Bencoolen": ["DT21"], "Jalan Besar": ["DT22"], "Bendemeer": ["DT23"],
    "Geylang Bahru": ["DT24"], "Mattar": ["DT25"], "Ubi": ["DT27"],
    "Kaki Bukit": ["DT28"], "Bedok North": ["DT29"], "Bedok Reservoir": ["DT30"],
    "Tampines West": ["DT31"], "Tampines East": ["DT33"], "Upper Changi": ["DT34"],
    # Thomson-East Coast Line
    "Woodlands North": ["TE1"], "Woodlands South": ["TE3"], "Springleaf": ["TE4"],
    "Lentor": ["TE5"], "Mayflower": ["TE6"], "Bright Hill": ["TE7"],
    "Upper Thomson": ["TE8"], "Stevens": ["TE11", "DT10"], "Napier": ["TE12"],
    "Orchard Boulevard": ["TE13"], "Great World": ["TE15"], "Havelock": ["TE16"],
    "Maxwell": ["TE18"], "Shenton Way": ["TE19"], "Gardens by the Bay": ["TE22"],
    "Tanjong Rhu": ["TE23"], "Katong Park": ["TE24"], "Tanjong Katong": ["TE25"],
    "Marine Parade": ["TE26"], "Marine Terrace": ["TE27"], "Siglap": ["TE28"],
    "Bayshore": ["TE29"],
    # Bukit Panjang LRT
    "South View": ["BP2"], "Keat Hong": ["BP3"], "Teck Whye": ["BP4"],
    "Phoenix": ["BP5"], "Petir": ["BP7"], "Pending": ["BP8"], "Bangkit": ["BP9"],
    "Fajar": ["BP10"], "Segar": ["BP11"], "Jelapang": ["BP12"], "Senja": ["BP13"],
    # Sengkang LRT
    "Compassvale": ["SE1"], "Rumbia": ["SE2"], "Bakau": ["SE3"], "Kangkar": ["SE4"],
    "Ranggung": ["SE5"], "Cheng Lim": ["SW1"], "Farmway": ["SW2"], "Kupang": ["SW3"],
    "Thanggam": ["SW4"], "Fernvale": ["SW5"], "Layar": ["SW6"], "Tongkang": ["SW7"],
    "Renjong": ["SW8"],
    # Punggol LRT
    "Cove": ["PE1"], "Meridian": ["PE2"], "Coral Edge": ["PE3"], "Riviera": ["PE4"],
    "Kadaloor": ["PE5"], "Oasis": ["PE6"], "Damai": ["PE7"], "Sam Kee": ["PW1"],
    "Teck Lee": ["PW2"], "Punggol Point": ["PW3"], "Samudera": ["PW4"],
    "Nibong": ["PW5"], "Sumang": ["PW6"], "Soo Teck": ["PW7"],
}

# Dash-style "tap-out transfer" pairs (exit and re-enter the paid area) —
# same physical building, but not a same-platform walk-through transfer.
TAP_OUT_PAIRS: set[frozenset[str]] = {
    frozenset({"NS21", "DT11"}),  # Newton
    frozenset({"BP6", "DT1"}),  # Bukit Panjang
}

_LINE_NAMES = {
    "NS": "NSL", "EW": "EWL", "CG": "EWL", "NE": "NEL", "CC": "CCL", "DT": "DTL",
    "TE": "TEL", "BP": "BPLRT", "SE": "SKLRT", "SW": "SKLRT", "PE": "PGLRT",
    # STC/PTC are the Sengkang/Punggol LRT-centre nodes. They share a
    # physical interchange with NE16/NE17 respectively, but their ride edges
    # belong to the LRT systems—not the North-East Line.
    "PW": "PGLRT", "STC": "SKLRT", "PTC": "PGLRT",
}


def _line_for_code(code: str) -> str:
    prefix = re.match(r"[A-Z]+", code)
    key = prefix.group() if prefix else code
    return _LINE_NAMES.get(key, key)


# ---------------------------------------------------------------------------
# 2. Ordered per-line sequences (ride order). Loop closures and the one
#    genuine branch (EWL -> Changi Airport Extension) are listed explicitly
#    rather than inferred from code numbers, since numeric adjacency alone
#    breaks at loop-closures/branches.
# ---------------------------------------------------------------------------
LINE_SEQUENCES: dict[str, list[str]] = {
    "NSL": [f"NS{n}" for n in range(1, 29) if n != 6],
    "EWL": [f"EW{n}" for n in range(1, 34)],
    "NEL": [f"NE{n}" for n in range(1, 19) if n != 2],
    "CCL_BRANCH": ["CC1", "CC2", "CC3", "CC4"],
    "CCL_LOOP": ["CC4"] + [f"CC{n}" for n in range(5, 35) if n != 18],
    "DTL": [f"DT{n}" for n in range(1, 36)],
    "TEL": [f"TE{n}" for n in range(1, 30) if n not in (10, 21)],
    "BPLRT_TRUNK": ["BP1", "BP2", "BP3", "BP4", "BP5", "BP6"],
    "BPLRT_LOOP": ["BP6", "BP7", "BP8", "BP9", "BP10", "BP11", "BP12", "BP13", "BP6"],
    "SKLRT_EAST": ["STC", "SE1", "SE2", "SE3", "SE4", "SE5", "STC"],
    "SKLRT_WEST": ["STC", "SW1", "SW2", "SW3", "SW4", "SW5", "SW6", "SW7", "SW8", "STC"],
    "PGLRT_EAST": ["PTC", "PE1", "PE2", "PE3", "PE4", "PE5", "PE6", "PE7", "PTC"],
    "PGLRT_WEST": ["PTC", "PW1", "PW2", "PW3", "PW4", "PW5", "PW6", "PW7", "PTC"],
}
# EWL -> Changi Airport Extension branch, off Tanah Merah (EW4)
EWL_CHANGI_BRANCH = ["EW4", "CG1", "CG2"]

RIDE_MINUTES_BY_LINE_GROUP = {
    "NSL": 2.5, "EWL": 2.5, "NEL": 2.5, "CCL_BRANCH": 2.0, "CCL_LOOP": 2.0,
    "DTL": 2.5, "TEL": 2.5, "BPLRT_TRUNK": 1.5, "BPLRT_LOOP": 1.5,
    "SKLRT_EAST": 1.5, "SKLRT_WEST": 1.5, "PGLRT_EAST": 1.5, "PGLRT_WEST": 1.5,
}
TRANSFER_MINUTES = 5.0
TAP_OUT_TRANSFER_MINUTES = 6.0


def build_structure_rows() -> list[dict]:
    rows: list[dict] = []
    for name, codes in STATIONS.items():
        for code in codes:
            rows.append(
                {
                    "station_code": code,
                    "station_name": name,
                    "line": _line_for_code(code),
                    "interchange_group": name.upper().replace(" ", "_"),
                }
            )
    return rows


def build_edges() -> list[dict]:
    edges: list[dict] = []
    seen_ride: set[frozenset[str]] = set()

    for group, sequence in LINE_SEQUENCES.items():
        minutes = RIDE_MINUTES_BY_LINE_GROUP[group]
        line_label = _line_for_code(sequence[0])
        for a, b in zip(sequence, sequence[1:]):
            key = frozenset({a, b})
            if key in seen_ride:
                continue
            seen_ride.add(key)
            edges.append({"from_node": a, "to_node": b, "line": line_label, "edge_type": "ride", "estimated_minutes": minutes})

    for a, b in zip(EWL_CHANGI_BRANCH, EWL_CHANGI_BRANCH[1:]):
        edges.append({"from_node": a, "to_node": b, "line": "EWL", "edge_type": "ride", "estimated_minutes": 2.5})

    # Transfer edges: every pair of codes sharing a physical station.
    for name, codes in STATIONS.items():
        if len(codes) < 2:
            continue
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                a, b = codes[i], codes[j]
                is_tap_out = frozenset({a, b}) in TAP_OUT_PAIRS
                minutes = TAP_OUT_TRANSFER_MINUTES if is_tap_out else TRANSFER_MINUTES
                edges.append(
                    {
                        "from_node": a,
                        "to_node": b,
                        "line": "INTERCHANGE" if not is_tap_out else "TAP_OUT_TRANSFER",
                        "edge_type": "transfer",
                        "estimated_minutes": minutes,
                    }
                )
    return edges


def geocode_stations() -> dict[str, dict]:
    """Geocode every physical station name via the live OneMap adapter."""
    from app.adapters.live.onemap import LiveOneMapAdapter

    adapter = LiveOneMapAdapter()
    coords: dict[str, dict] = {}
    failures: list[str] = []
    lrt_lines = {"BPLRT", "SKLRT", "PGLRT"}
    for idx, (name, codes) in enumerate(STATIONS.items()):
        lines = {_line_for_code(c) for c in codes}
        is_lrt_only = lines and lines.issubset(lrt_lines)
        query = f"{name} LRT STATION" if is_lrt_only else f"{name} MRT STATION"
        try:
            result = adapter.geocode(query)
            coords[name] = {"latitude": result.latitude, "longitude": result.longitude}
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc}")
            coords[name] = {"latitude": None, "longitude": None}
        if idx % 20 == 0:
            print(f"  geocoded {idx}/{len(STATIONS)}...")
        time.sleep(0.05)
    if failures:
        print(f"WARNING: {len(failures)} stations failed to geocode:")
        for f in failures[:20]:
            print(f"  - {f}")
    return coords


def main() -> int:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    structure_rows = build_structure_rows()
    with STRUCTURE_CSV.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["station_code", "station_name", "line", "interchange_group"])
        writer.writeheader()
        writer.writerows(structure_rows)
    print(f"Wrote {len(structure_rows)} station-line rows to {STRUCTURE_CSV}")

    print("Geocoding stations via OneMap (live)...")
    coords = geocode_stations()

    stations_out = []
    for name, codes in STATIONS.items():
        c = coords.get(name, {})
        stations_out.append(
            {
                "station_name": name,
                "codes": codes,
                "lines": sorted({_line_for_code(code) for code in codes}),
                "is_interchange": len(codes) > 1,
                "latitude": c.get("latitude"),
                "longitude": c.get("longitude"),
                "active": True,
            }
        )
    STATIONS_JSON.write_text(
        json.dumps(
            {"source": RAIL_DATA_SOURCE, "version": RAIL_DATA_VERSION, "stations": stations_out},
            indent=2,
        )
    )
    print(f"Wrote {len(stations_out)} geocoded stations to {STATIONS_JSON}")

    edges = build_edges()
    with EDGES_CSV.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["from_node", "to_node", "line", "edge_type", "estimated_minutes"])
        writer.writeheader()
        writer.writerows(edges)
    print(f"Wrote {len(edges)} edges to {EDGES_CSV}")

    missing_coords = [s["station_name"] for s in stations_out if s["latitude"] is None]
    if missing_coords:
        print(f"WARNING: {len(missing_coords)} stations missing coordinates: {missing_coords}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
