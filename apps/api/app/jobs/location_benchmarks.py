"""Free backfill, refresh and publication of block-level location benchmarks.

Run from ``apps/api``:

  python -m app.jobs.location_benchmarks load-registry
  python -m app.jobs.location_benchmarks status
  python -m app.jobs.location_benchmarks measure --features public_transport --town BH --limit 20
  python -m app.jobs.location_benchmarks measure --features driving_baseline --max-requests 2000
  python -m app.jobs.location_benchmarks publish
  python -m app.jobs.location_benchmarks refresh

Only the free feature sets (``public_transport`` and ``driving_baseline``)
can be measured here, and only through the free routing provider (OneMap).
This job never constructs a paid provider and refuses to run if the free
provider is anything else, so it makes zero Google calls. Google route
flexibility and peak-hour evidence exist only for shortlisted homes in the
normal enrichment workflow and are never backfilled or ranked.

``measure`` skips blocks whose stored result is current (same calculation
version — which includes provider and time assumptions — same reference-data
fingerprint, and within its maximum age), so re-running it resumes an
interrupted backfill and is also the incremental refresh after source data
changes. ``--max-requests`` bounds OneMap usage per run. ``publish`` rebuilds
every comparison from stored measurements without calling any provider.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.adapters.reference_data import FIXTURES
from app.core.config import settings
from app.db.session import SessionLocal
from app.repositories.block_repository import BlockRepository
from app.services.block_features import (
    FREE_FEATURE_SETS,
    BlockFeatureService,
    calculation_version,
    source_fingerprint,
)
from app.services.location_benchmarks import build_and_publish

FREE_PROVIDERS = {"ONEMAP_ROUTING", "MOCK_ROUTING", "FREE_ROUTING_UNAVAILABLE"}


class PaidProviderRefused(RuntimeError):
    pass


def free_routing_provider():
    """The only provider this job may use; a paid provider is refused outright."""
    from app.adapters.factory import get_free_routing_provider

    provider = get_free_routing_provider()
    if provider.provider_name not in FREE_PROVIDERS:
        raise PaidProviderRefused(f"Refusing to backfill with {provider.provider_name}")
    return provider


def compute_function(feature_set: str, block: Any, routing: Any, availability: Any, carpark_repo: Any):
    from app.engines.driving.engine import compute_driving_baseline
    from app.engines.public_transport.engine import compute_public_transport_model
    from app.services.enrichment_service import _rollup_to_field

    if feature_set == "public_transport":
        return lambda lat, lng: {
            **_rollup_to_field(compute_public_transport_model(lat, lng, routing)),
            "routing_provider": routing.provider_name,
        }
    if feature_set == "driving_baseline":
        return lambda lat, lng: {
            "components": [
                component.to_dict()
                for component in compute_driving_baseline(
                    lat,
                    lng,
                    routing,
                    availability_provider=availability,
                    history_lookup=carpark_repo.historical_summary,
                    listing_address=f"{block.block} {block.street}",
                )
            ],
            "routing_provider": routing.provider_name,
        }
    raise ValueError(f"{feature_set} is not a free block feature set")


def cmd_load_registry(args: argparse.Namespace) -> dict[str, Any]:
    payload = json.loads(Path(args.fixture).read_text())
    with SessionLocal() as db:
        count = BlockRepository(db).upsert_registry(payload["blocks"], payload["metadata"])
        db.commit()
    return {"loaded_blocks": count, "metadata": {k: v for k, v in payload["metadata"].items() if k != "town_legend"}}


def cmd_status(_args: argparse.Namespace) -> dict[str, Any]:
    with SessionLocal() as db:
        repo = BlockRepository(db)
        blocks = repo.residential_blocks()
        keys = {b.block_key for b in blocks}
        features: dict[str, Any] = {}
        for feature_set in FREE_FEATURE_SETS:
            version = calculation_version(feature_set)
            fingerprint = source_fingerprint(feature_set)
            current = repo.feature_keys(feature_set, version, fingerprint) & keys
            features[feature_set] = {
                "calculation_version": version,
                "source_fingerprint": fingerprint,
                "current_blocks": len(current),
                "coverage": round(len(current) / len(blocks), 4) if blocks else 0,
            }
        published = repo.published_version()
        return {
            "residential_blocks": len(blocks),
            "geocoded_blocks": sum(1 for b in blocks if b.latitude is not None),
            "features": features,
            "published_version": str(published.id) if published else None,
            "published_at": published.published_at.isoformat() if published and published.published_at else None,
        }


def cmd_measure(args: argparse.Namespace) -> dict[str, Any]:
    feature_sets = [f.strip() for f in args.features.split(",") if f.strip()]
    for feature_set in feature_sets:
        if feature_set not in FREE_FEATURE_SETS:
            raise SystemExit(
                f"{feature_set} cannot be backfilled: only {', '.join(FREE_FEATURE_SETS)} are free block measurements."
            )
    if settings.demo_mode:
        print("WARNING: DEMO_MODE=true; results are demo data and will never be published.", file=sys.stderr)

    from app.adapters.parking.hdb_availability import CarparkAvailabilityProvider
    from app.repositories.carpark_repository import CarparkRepository

    routing = free_routing_provider()
    report: dict[str, Any] = {"routing_provider": routing.provider_name}
    try:
        with SessionLocal() as db:
            repo = BlockRepository(db)
            service = BlockFeatureService(repo)
            carpark_repo = CarparkRepository(db)
            availability = CarparkAvailabilityProvider() if "driving_baseline" in feature_sets else None
            blocks = [b for b in repo.residential_blocks() if b.latitude is not None and b.longitude is not None]
            if args.town:
                blocks = [b for b in blocks if b.town_code == args.town]
            budget_hit = False
            for feature_set in feature_sets:
                todo = [b for b in blocks if args.force or service.cached(b.block_key, feature_set) is None]
                if args.limit:
                    todo = todo[: args.limit]
                statuses: Counter[str] = Counter()
                for index, block in enumerate(todo, start=1):
                    if args.max_requests and getattr(routing, "request_count", 0) >= args.max_requests:
                        budget_hit = True
                        break
                    try:
                        outcome = service.get_or_compute(
                            block.block_key,
                            feature_set,
                            (block.latitude, block.longitude),
                            "hdb_registry_onemap",
                            compute_function(feature_set, block, routing, availability, carpark_repo),
                            computed_for="backfill",
                            reuse=False,
                        )
                        db.commit()
                        statuses[_outcome_status(outcome.payload)] += 1
                    except Exception as exc:  # one block failing must not stop the backfill
                        db.rollback()
                        statuses[f"error:{type(exc).__name__}"] += 1
                    if index % 100 == 0:
                        print(f"{feature_set}: {index}/{len(todo)}", file=sys.stderr, flush=True)
                report[feature_set] = {"blocks_selected": len(todo), "outcomes": dict(statuses)}
                if budget_hit:
                    report["stopped"] = f"request budget of {args.max_requests} reached; re-run to resume"
                    break
            report["onemap_requests"] = getattr(routing, "request_count", None)
    finally:
        close = getattr(routing, "close", None)
        if callable(close):
            close()
    return report


def _outcome_status(payload: dict[str, Any]) -> str:
    statuses = sorted({str(c.get("status")) for c in payload.get("components") or []})
    return "+".join(statuses) or "empty"


def cmd_publish(args: argparse.Namespace) -> dict[str, Any]:
    with SessionLocal() as db:
        result = build_and_publish(db, publish=not args.no_publish)
    measurements = result["summary"]["measurements"]
    return {
        "version_id": result["version_id"],
        "published": result["published"],
        "results": result["results"],
        "measurements": {
            key: {
                "valid_blocks": value["valid_blocks"],
                "national_coverage": value["national_coverage"],
                "national_comparisons_available": value["national_comparisons_available"],
                "towns_meeting_rule": len(value["towns_meeting_rule"]),
            }
            for key, value in measurements.items()
        },
    }


def cmd_refresh(args: argparse.Namespace) -> dict[str, Any]:
    """Re-measure stale/missing blocks, then publish one coherent version."""
    measured = cmd_measure(args)
    if args.no_publish:
        return {"measure": measured}
    return {"measure": measured, "publish": cmd_publish(argparse.Namespace(no_publish=False))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    load = sub.add_parser("load-registry", help="Load data_pipeline/fixtures/hdb_blocks.json into PostgreSQL")
    load.add_argument("--fixture", default=str(Path(FIXTURES) / "hdb_blocks.json"))
    load.set_defaults(func=cmd_load_registry)

    sub.add_parser("status", help="Show registry and measurement coverage").set_defaults(func=cmd_status)

    for name, func in (("measure", cmd_measure), ("refresh", cmd_refresh)):
        command = sub.add_parser(name)
        command.add_argument("--features", default=",".join(FREE_FEATURE_SETS))
        command.add_argument("--town", help="Limit to one HDB town code, e.g. AMK")
        command.add_argument("--limit", type=int, default=0, help="Maximum blocks per feature set this run")
        command.add_argument("--max-requests", type=int, default=0, help="Stop after this many OneMap requests")
        command.add_argument("--force", action="store_true", help="Recompute even current results")
        command.add_argument("--no-publish", action="store_true")
        command.set_defaults(func=func)

    publish = sub.add_parser("publish", help="Build and publish a benchmark version from stored measurements")
    publish.add_argument("--no-publish", action="store_true", help="Build for inspection without publishing")
    publish.set_defaults(func=cmd_publish)

    args = parser.parse_args(argv)
    print(json.dumps(args.func(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
