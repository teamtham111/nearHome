"""Run the complete NearHomeV2 batch/reference data pipeline.

Examples:
  apps/api/.venv/bin/python data_pipeline/run_pipeline.py --list-sources
  apps/api/.venv/bin/python data_pipeline/run_pipeline.py --profile validate
  apps/api/.venv/bin/python data_pipeline/run_pipeline.py --profile all --dry-run
  apps/api/.venv/bin/python data_pipeline/run_pipeline.py --profile core
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_pipeline.data_gov_client import write_json_atomic
from data_pipeline.source_catalog import SOURCES, source_dicts

PIPELINE_VERSION = "1.0.0"
RAW = ROOT / "data_pipeline" / "snapshots" / "raw"
MANIFESTS = ROOT / "data_pipeline" / "manifests"
FIXTURES = ROOT / "data_pipeline" / "fixtures"
API_ROOT = ROOT / "apps" / "api"
HDB_TRANSACTION_DATASET = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
MOE_SCHOOL_DATASET = "d_688b934f82c1059ed0a6993d2a829089"
SLA_LINE_DATASET = "d_10480c0b59e65663dfae1028ff4aa8bb"
Profile = Literal["validate", "core", "roads", "all", "realtime"]


@dataclass(frozen=True)
class Stage:
    id: str
    description: str
    profiles: frozenset[Profile]
    command: tuple[str, ...]
    source_ids: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    credentials: tuple[str, ...] = ()
    outputs: tuple[Path, ...] = ()
    cwd: Path = ROOT


def _python_script(name: str, *args: object) -> tuple[str, ...]:
    return (sys.executable, str(ROOT / "data_pipeline" / name), *(str(arg) for arg in args))


STAGES: tuple[Stage, ...] = (
    Stage(
        "download-hdb-transactions",
        "Download the complete official HDB resale transaction CSV",
        frozenset({"core", "all"}),
        _python_script(
            "download_data_gov.py",
            HDB_TRANSACTION_DATASET,
            RAW / "hdb_resale_transactions.csv",
            "--expected-suffix",
            ".csv",
        ),
        source_ids=("hdb-resale-transactions",),
        outputs=(RAW / "hdb_resale_transactions.csv",),
    ),
    Stage(
        "normalize-hdb-transactions",
        "Validate and normalize HDB transactions for comparables/model training",
        frozenset({"core", "all"}),
        _python_script(
            "ingest_hdb_transactions.py",
            RAW / "hdb_resale_transactions.csv",
            "--output",
            FIXTURES / "hdb_transactions.json",
            "--min-month",
            "",
        ),
        source_ids=("hdb-resale-transactions",),
        dependencies=("download-hdb-transactions",),
        outputs=(FIXTURES / "hdb_transactions.json",),
    ),
    Stage(
        "refresh-hdb-carparks",
        "Refresh static HDB carpark attributes and mirror them into PostgreSQL",
        frozenset({"core", "all"}),
        _python_script("ingest_hdb_carparks.py", "--live", "--persist-db"),
        source_ids=("hdb-carpark-information", "postgresql"),
        credentials=("DATABASE_URL",),
        outputs=(FIXTURES / "hdb_carparks.json",),
    ),
    Stage(
        "refresh-hdb-blocks",
        "Refresh the HDB residential block registry (HDB Property Information + OneMap) for benchmarks",
        frozenset({"core", "all"}),
        _python_script("ingest_hdb_blocks.py", "--live", "--persist-db"),
        source_ids=("hdb-property-information", "onemap-geocoding", "postgresql"),
        credentials=("ONEMAP_EMAIL", "ONEMAP_PASSWORD", "DATABASE_URL"),
        outputs=(FIXTURES / "hdb_blocks.json",),
    ),
    Stage(
        "download-moe-schools",
        "Download the complete official MOE school directory CSV",
        frozenset({"core", "all"}),
        _python_script(
            "download_data_gov.py",
            MOE_SCHOOL_DATASET,
            RAW / "moe_general_information_of_schools.csv",
            "--expected-suffix",
            ".csv",
        ),
        source_ids=("moe-school-directory",),
        outputs=(RAW / "moe_general_information_of_schools.csv",),
    ),
    Stage(
        "normalize-moe-schools",
        "Normalize MOE schools and geocode changed addresses with OneMap",
        frozenset({"core", "all"}),
        _python_script("ingest_moe_schools.py", RAW / "moe_general_information_of_schools.csv"),
        source_ids=("moe-school-directory", "onemap-geocoding"),
        dependencies=("download-moe-schools",),
        credentials=("ONEMAP_EMAIL", "ONEMAP_PASSWORD"),
        outputs=(FIXTURES / "moe_schools.json",),
    ),
    Stage(
        "refresh-lta-bus-stops",
        "Refresh LTA DataMall BusStops",
        frozenset({"core", "all"}),
        _python_script("ingest_lta_reference.py", "--live"),
        source_ids=("lta-bus-stops",),
        credentials=("LTA_ACCOUNT_KEY",),
        outputs=(FIXTURES / "lta_bus_stops.json",),
    ),
    Stage(
        "refresh-lta-bus-routes",
        "Refresh LTA DataMall BusRoutes",
        frozenset({"core", "all"}),
        _python_script("ingest_bus_routes.py", "--live"),
        source_ids=("lta-bus-routes",),
        credentials=("LTA_ACCOUNT_KEY",),
        outputs=(FIXTURES / "lta_bus_routes.json",),
    ),
    Stage(
        "refresh-lta-bus-services",
        "Refresh LTA DataMall BusServices",
        frozenset({"core", "all"}),
        _python_script("ingest_bus_services.py", "--live"),
        source_ids=("lta-bus-services",),
        credentials=("LTA_ACCOUNT_KEY",),
        outputs=(FIXTURES / "lta_bus_services.json",),
    ),
    Stage(
        "build-bus-index",
        "Join LTA snapshots and publish the bus data-quality report",
        frozenset({"core", "all"}),
        _python_script("build_bus_indexes.py"),
        source_ids=("lta-bus-stops", "lta-bus-routes", "lta-bus-services", "bus-index"),
        dependencies=("refresh-lta-bus-stops", "refresh-lta-bus-routes", "refresh-lta-bus-services"),
        outputs=(FIXTURES / "bus_data_quality_report.json",),
    ),
    Stage(
        "build-rail-graph",
        "Rebuild the curated rail topology and OneMap station coordinates",
        frozenset({"core", "all"}),
        _python_script("build_rail_graph.py"),
        source_ids=("rail-structure", "onemap-geocoding"),
        credentials=("ONEMAP_EMAIL", "ONEMAP_PASSWORD"),
        outputs=(
            FIXTURES / "rail/rail_lines_structure.csv",
            FIXTURES / "rail/rail_stations.json",
            FIXTURES / "rail/rail_edges.csv",
        ),
    ),
    Stage(
        "validate-transport",
        "Apply cross-file bus and rail semantic quality gates",
        frozenset({"validate", "core", "all"}),
        _python_script("validate_transport_data.py"),
        source_ids=("bus-index", "rail-structure"),
        dependencies=("build-bus-index", "build-rail-graph"),
    ),
    Stage(
        "download-sla-national-map",
        "Download the official SLA National Map Line GeoJSON",
        frozenset({"roads", "all"}),
        _python_script(
            "download_data_gov.py",
            SLA_LINE_DATASET,
            RAW / "sla_national_map_line.geojson",
            "--skip-initiate",
            "--expected-suffix",
            ".geojson",
        ),
        source_ids=("sla-national-map-line",),
        outputs=(RAW / "sla_national_map_line.geojson",),
    ),
    Stage(
        "filter-sla-major-roads",
        "Filter the official National Map Line to Layers/Major_Road",
        frozenset({"roads", "all"}),
        _python_script("ingest_sla_major_roads.py", RAW / "sla_national_map_line.geojson"),
        source_ids=("sla-national-map-line",),
        dependencies=("download-sla-national-map",),
        outputs=(FIXTURES / "sla_major_roads.geojson",),
    ),
    Stage(
        "build-osm-drive-graph",
        "Download and persist the OpenStreetMap Singapore drive graph",
        frozenset({"roads", "all"}),
        _python_script("build_singapore_drive_graph.py"),
        source_ids=("openstreetmap-drive-graph",),
        outputs=(FIXTURES / "singapore-drive.graphml",),
    ),
    Stage(
        "build-major-road-map",
        "Map official SLA Major Roads to OSM edges and legal entry nodes",
        frozenset({"roads", "all"}),
        _python_script("build_major_road_mapping.py"),
        source_ids=("sla-national-map-line", "openstreetmap-drive-graph", "sla-osm-major-road-map"),
        dependencies=("filter-sla-major-roads", "build-osm-drive-graph"),
        outputs=(FIXTURES / "sla_osm_major_road_mapping.json",),
    ),
    Stage(
        "validate-major-road-map",
        "Run exhaustive/STRtree mapping diagnostics and gold-label evaluation",
        frozenset({"roads", "all"}),
        _python_script("validate_major_road_mapping.py"),
        source_ids=("sla-osm-major-road-map",),
        dependencies=("build-major-road-map",),
    ),
    Stage(
        "train-fair-price-model",
        "Train and atomically publish the CatBoost fair-price artifact",
        frozenset({"core", "all"}),
        (sys.executable, "-m", "app.engines.fair_price_catboost", "train", "--artifact-dir", str(ROOT / "artifacts/fair_price/catboost")),
        source_ids=("hdb-resale-transactions", "catboost-fair-price-model"),
        dependencies=("normalize-hdb-transactions",),
        outputs=(ROOT / "artifacts/fair_price/catboost/model.cbm", ROOT / "artifacts/fair_price/catboost/metadata.json"),
        cwd=API_ROOT,
    ),
    Stage(
        "validate-all-snapshots",
        "Validate every production snapshot and artifact against schema and volume gates",
        frozenset({"validate", "core", "roads", "all"}),
        _python_script("validate_snapshots.py"),
        source_ids=tuple(source.id for source in SOURCES if source.kind in {"official", "open", "derived"}),
        dependencies=("validate-transport", "validate-major-road-map", "train-fair-price-model"),
    ),
    Stage(
        "collect-hdb-availability",
        "Collect one HDB carpark availability observation into PostgreSQL",
        frozenset({"realtime"}),
        _python_script("ingest_hdb_availability.py"),
        source_ids=("hdb-carpark-availability", "postgresql"),
        credentials=("DATABASE_URL",),
    ),
)


@dataclass
class StageResult:
    id: str
    description: str
    status: Literal["success", "failed", "blocked", "dry_run"]
    started_at: str
    finished_at: str
    duration_seconds: float
    command: list[str]
    source_ids: list[str]
    exit_code: int | None
    output_artifacts: list[dict[str, object]] = field(default_factory=list)
    error: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        if key and key.replace("_", "").isalnum():
            values[key] = value
    return values


def child_environment() -> dict[str, str]:
    env = _load_env_file(ROOT / ".env")
    env.update(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(API_ROOT), str(ROOT), env.get("PYTHONPATH", "")) if part
    )
    return env


def _hash(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": str(path.relative_to(ROOT)), "bytes": size, "sha256": digest.hexdigest()}


def _stage_artifacts(stage: Stage) -> list[dict[str, object]]:
    return [_hash(path) for path in stage.outputs if path.is_file()]


def selected_stages(profile: Profile, only: set[str] | None = None) -> list[Stage]:
    selected = [stage for stage in STAGES if profile in stage.profiles]
    if only:
        unknown = only - {stage.id for stage in STAGES}
        if unknown:
            raise ValueError(f"Unknown stage(s): {', '.join(sorted(unknown))}")
        selected = [stage for stage in selected if stage.id in only]
    return selected


def _missing_credentials(stage: Stage, env: dict[str, str]) -> list[str]:
    return [name for name in stage.credentials if not env.get(name, "").strip()]


def run(profile: Profile, *, dry_run: bool, only: set[str] | None, continue_on_error: bool) -> dict[str, object]:
    stages = selected_stages(profile, only)
    env = child_environment()
    run_started = _utc_now()
    run_id = run_started.strftime("%Y%m%dT%H%M%SZ")
    results: list[StageResult] = []
    state: dict[str, str] = {}

    for stage in stages:
        started = _utc_now()
        selected_ids = {item.id for item in stages}
        failed_dependencies = [
            dependency
            for dependency in stage.dependencies
            if dependency in selected_ids and state.get(dependency) not in {"success", "dry_run"}
        ]
        missing = _missing_credentials(stage, env)
        error = None
        status: Literal["success", "failed", "blocked", "dry_run"]
        exit_code: int | None = None
        if failed_dependencies:
            status = "blocked"
            error = f"dependency failed: {', '.join(failed_dependencies)}"
        elif missing:
            status = "blocked"
            error = f"missing required environment variable(s): {', '.join(missing)}"
        elif dry_run:
            status = "dry_run"
            print(f"\n[{stage.id}] DRY RUN - {stage.description}", flush=True)
            print("$ " + shlex.join(stage.command), flush=True)
        else:
            print(f"\n[{stage.id}] {stage.description}", flush=True)
            print("$ " + shlex.join(stage.command), flush=True)
            try:
                completed = subprocess.run(stage.command, cwd=stage.cwd, env=env, check=False)
                exit_code = completed.returncode
                status = "success" if completed.returncode == 0 else "failed"
                error = None if completed.returncode == 0 else f"command exited with status {completed.returncode}"
            except OSError as exc:
                status = "failed"
                error = f"could not start command: {type(exc).__name__}"
        finished = _utc_now()
        state[stage.id] = status
        result = StageResult(
            id=stage.id,
            description=stage.description,
            status=status,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_seconds=round((finished - started).total_seconds(), 3),
            command=list(stage.command),
            source_ids=list(stage.source_ids),
            exit_code=exit_code,
            output_artifacts=_stage_artifacts(stage) if status == "success" else [],
            error=error,
        )
        results.append(result)
        if error:
            print(f"[{stage.id}] {status.upper()}: {error}", file=sys.stderr, flush=True)
        if status in {"failed", "blocked"} and not continue_on_error:
            break

    run_finished = _utc_now()
    statuses = {result.status for result in results}
    overall = "failed" if statuses & {"failed", "blocked"} else "dry_run" if dry_run else "success"
    manifest: dict[str, object] = {
        "schema_version": 1,
        "pipeline_version": PIPELINE_VERSION,
        "run_id": run_id,
        "profile": profile,
        "status": overall,
        "started_at": run_started.isoformat(),
        "finished_at": run_finished.isoformat(),
        "duration_seconds": round((run_finished - run_started).total_seconds(), 3),
        "source_ids": [source.id for source in SOURCES],
        "stages": [asdict(result) for result in results],
    }
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    write_json_atomic(MANIFESTS / f"{run_id}.json", manifest)
    write_json_atomic(MANIFESTS / "latest.json", manifest)
    return manifest


def print_sources(as_json: bool) -> None:
    if as_json:
        print(json.dumps(source_dicts(), indent=2, sort_keys=True))
        return
    print(f"{'ID':30} {'KIND':11} {'ACCESS':10} NAME")
    for source in SOURCES:
        print(f"{source.id:30} {source.kind:11} {source.access:10} {source.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("validate", "core", "roads", "all", "realtime"), default="validate")
    parser.add_argument("--dry-run", action="store_true", help="Print/preflight stages and write a manifest without running them")
    parser.add_argument("--only", action="append", default=[], metavar="STAGE", help="Run only a selected stage (repeatable)")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue independent stages after a failure")
    parser.add_argument("--list-sources", action="store_true", help="Show the canonical complete source inventory and exit")
    parser.add_argument("--json", action="store_true", help="Use JSON output with --list-sources")
    args = parser.parse_args()
    if args.list_sources:
        print_sources(args.json)
        return 0
    try:
        manifest = run(
            args.profile,
            dry_run=args.dry_run,
            only=set(args.only) or None,
            continue_on_error=args.continue_on_error,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(f"\nPipeline {manifest['status']}; manifest: {MANIFESTS / 'latest.json'}")
    return 0 if manifest["status"] in {"success", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
