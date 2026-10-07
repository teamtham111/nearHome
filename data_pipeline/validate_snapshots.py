"""Validate every persisted NearHomeV2 reference snapshot and model artifact."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.adapters.provider_schemas.artifacts import (
    FairPriceModelMetadata,
    MajorRoadMappingArtifact,
    SlaMajorRoadFeatureCollection,
)
from app.adapters.provider_schemas.hdb import (
    HdbCarparkSnapshotRow,
    HdbTransactionSnapshotRow,
)
from app.adapters.provider_schemas.reference import (
    LtaBusRouteSnapshotRow,
    LtaBusServiceSnapshotRow,
    LtaBusStopSnapshotRow,
    MoeSchoolSnapshotRow,
    RailEdgeSnapshotRow,
    RailStationSnapshot,
)


@dataclass(frozen=True)
class Check:
    name: str
    path: Path
    minimum_rows: int
    adapter: object


CHECKS = (
    Check("HDB resale transactions", ROOT / "data_pipeline/fixtures/hdb_transactions.json", 10_000, TypeAdapter(list[HdbTransactionSnapshotRow])),
    Check("HDB carparks", ROOT / "data_pipeline/fixtures/hdb_carparks.json", 1_000, TypeAdapter(list[HdbCarparkSnapshotRow])),
    Check("LTA bus stops", ROOT / "data_pipeline/fixtures/lta_bus_stops.json", 4_000, TypeAdapter(list[LtaBusStopSnapshotRow])),
    Check("LTA bus routes", ROOT / "data_pipeline/fixtures/lta_bus_routes.json", 20_000, TypeAdapter(list[LtaBusRouteSnapshotRow])),
    Check("LTA bus services", ROOT / "data_pipeline/fixtures/lta_bus_services.json", 500, TypeAdapter(list[LtaBusServiceSnapshotRow])),
    Check("MOE schools", ROOT / "data_pipeline/fixtures/moe_schools.json", 300, TypeAdapter(list[MoeSchoolSnapshotRow])),
)


def _row_checks(check: Check) -> tuple[bool, str]:
    if not check.path.is_file():
        return False, "missing"
    try:
        rows = check.adapter.validate_json(check.path.read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    except (OSError, ValidationError, ValueError) as exc:
        return False, f"invalid ({type(exc).__name__})"
    count = len(rows)
    return count >= check.minimum_rows, f"{count} rows (minimum {check.minimum_rows})"


def _artifact_checks() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    artifacts: tuple[tuple[str, Path, type], ...] = (
        ("SLA Major Roads", ROOT / "data_pipeline/fixtures/sla_major_roads.geojson", SlaMajorRoadFeatureCollection),
        ("SLA-to-OSM mapping", ROOT / "data_pipeline/fixtures/sla_osm_major_road_mapping.json", MajorRoadMappingArtifact),
        ("rail stations", ROOT / "data_pipeline/fixtures/rail/rail_stations.json", RailStationSnapshot),
        ("CatBoost metadata", ROOT / "artifacts/fair_price/catboost/metadata.json", FairPriceModelMetadata),
    )
    for name, path, model in artifacts:
        if not path.is_file():
            results.append((name, False, "missing"))
            continue
        try:
            model.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            results.append((name, False, f"invalid ({type(exc).__name__})"))
        else:
            results.append((name, True, "schema valid"))

    graph = ROOT / "data_pipeline/fixtures/singapore-drive.graphml"
    results.append(("Singapore drive graph", graph.is_file() and graph.stat().st_size > 1_000, f"{graph.stat().st_size if graph.exists() else 0} bytes"))
    edges = ROOT / "data_pipeline/fixtures/rail/rail_edges.csv"
    try:
        import csv

        with edges.open(newline="", encoding="utf-8") as handle:
            parsed = TypeAdapter(list[RailEdgeSnapshotRow]).validate_python(list(csv.DictReader(handle)))
        results.append(("rail edges", len(parsed) >= 100, f"{len(parsed)} rows"))
    except (OSError, ValidationError, ValueError) as exc:
        results.append(("rail edges", False, f"invalid ({type(exc).__name__})"))
    return results


def validate_all() -> list[tuple[str, bool, str]]:
    results = [(check.name, *_row_checks(check)) for check in CHECKS]
    results.extend(_artifact_checks())
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable results")
    args = parser.parse_args()
    results = validate_all()
    if args.json:
        print(json.dumps([{"name": name, "ok": ok, "detail": detail} for name, ok, detail in results], indent=2))
    else:
        for name, ok, detail in results:
            print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
