"""Backfill, refresh and publish block-level location benchmarks.

Run from ``apps/api``:

  python -m app.jobs.location_benchmarks load-registry
  python -m app.jobs.location_benchmarks status
  python -m app.jobs.location_benchmarks measure --features schools
  python -m app.jobs.location_benchmarks measure --features public_transport --allow-paid-routing --limit 50
  python -m app.jobs.location_benchmarks publish
  python -m app.jobs.location_benchmarks refresh            # offline features, then publish

``measure`` skips blocks whose stored result is current (same calculation
version, same reference-data fingerprint, not past its maximum age), so it is
both the initial backfill and the incremental refresh after source data
changes. Public Transport and Driving call the paid Google routing provider
for every block; they only run with ``--allow-paid-routing``. ``publish``
rebuilds every comparison from stored measurements without calling any
provider, so a ranking-only change never recalculates measurements.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.adapters.reference_data import FIXTURES
from app.core.config import settings
from app.db.session import SessionLocal
from app.repositories.block_repository import BlockRepository
from app.services.block_features import (
    FEATURE_SETS,
    BlockFeatureService,
    calculation_version,
    schools_block_payload,
    source_fingerprint,
)
from app.services.location_benchmarks import build_and_publish

PAID_FEATURES = {"public_transport", "driving"}
OFFLINE_FEATURES = ("schools",)
# Upper bounds from the engine configuration, for cost disclosure only.
MAX_ROUTE_CALLS_PER_BLOCK = {"public_transport": 37, "driving": 30}


class CountingRoutingProvider:
    """Delegate to a real provider while counting calls for cost reporting."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: Counter[str] = Counter()
        self.provider_name = getattr(inner, "provider_name", "UNKNOWN")

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr) or not name.startswith("get_"):
            return attr

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            return attr(*args, **kwargs)

        return counted


def _compute_fn(feature_set: str, block: Any, routing: Any, availability: Any, carpark_repo: Any):
    from app.engines.driving.engine import compute_driving_model
    from app.engines.public_transport.engine import compute_public_transport_model
    from app.services.enrichment_service import _rollup_to_field

    if feature_set == "schools":
        return schools_block_payload
    if feature_set == "public_transport":
        return lambda lat, lng: _rollup_to_field(compute_public_transport_model(lat, lng, routing))
    return lambda lat, lng: _rollup_to_field(
        compute_driving_model(
            lat,
            lng,
            routing,
            availability_provider=availability,
            history_lookup=carpark_repo.historical_summary,
            listing_address=f"{block.block} {block.street}",
        )
    )


def _is_current(repo: BlockRepository, block_key: str, feature_set: str, version: str, fingerprint: str) -> bool:
    row = repo.get_feature(block_key, feature_set, version)
    if row is None or row.source_fingerprint != fingerprint:
        return False
    max_age = {
        "public_transport": timedelta(days=settings.block_feature_max_age_days_public_transport),
        "driving": timedelta(days=settings.block_feature_max_age_days_driving),
    }.get(feature_set)
    computed_at = row.computed_at if row.computed_at.tzinfo else row.computed_at.replace(tzinfo=UTC)
    return max_age is None or datetime.now(UTC) - computed_at <= max_age


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
        geocoded = [b for b in blocks if b.latitude is not None]
        features: dict[str, Any] = {}
        for feature_set in FEATURE_SETS:
            version = calculation_version(feature_set)
            fingerprint = source_fingerprint(feature_set)
            current = repo.feature_keys(feature_set, version, fingerprint) & {b.block_key for b in blocks}
            features[feature_set] = {
                "calculation_version": version,
                "source_fingerprint": fingerprint,
                "current_blocks": len(current),
                "coverage": round(len(current) / len(blocks), 4) if blocks else 0,
            }
        published = repo.published_version()
        return {
            "residential_blocks": len(blocks),
            "geocoded_blocks": len(geocoded),
            "features": features,
            "published_version": str(published.id) if published else None,
            "published_at": published.published_at.isoformat() if published and published.published_at else None,
        }


def cmd_measure(args: argparse.Namespace) -> dict[str, Any]:
    feature_sets = [f.strip() for f in args.features.split(",") if f.strip()]
    for feature_set in feature_sets:
        if feature_set not in FEATURE_SETS:
            raise SystemExit(f"Unknown feature set {feature_set}")
        if feature_set in PAID_FEATURES and not args.allow_paid_routing:
            raise SystemExit(
                f"{feature_set} calls the paid Google routing provider for every block "
                f"(up to {MAX_ROUTE_CALLS_PER_BLOCK[feature_set]} calls per block). "
                "Re-run with --allow-paid-routing (and usually --limit) to proceed."
            )
    if settings.demo_mode:
        print("WARNING: DEMO_MODE=true; results are demo data and will never be published.", file=sys.stderr)

    from app.adapters.factory import get_routing_provider
    from app.adapters.parking.hdb_availability import CarparkAvailabilityProvider
    from app.repositories.carpark_repository import CarparkRepository

    routing = CountingRoutingProvider(get_routing_provider()) if set(feature_sets) & PAID_FEATURES else None
    report: dict[str, Any] = {}
    with SessionLocal() as db:
        repo = BlockRepository(db)
        service = BlockFeatureService(repo)
        carpark_repo = CarparkRepository(db)
        availability = CarparkAvailabilityProvider() if "driving" in feature_sets else None
        blocks = [b for b in repo.residential_blocks() if b.latitude is not None and b.longitude is not None]
        if args.town:
            blocks = [b for b in blocks if b.town_code == args.town]
        for feature_set in feature_sets:
            version = calculation_version(feature_set)
            fingerprint = source_fingerprint(feature_set)
            todo = [
                b for b in blocks if args.force or not _is_current(repo, b.block_key, feature_set, version, fingerprint)
            ]
            if args.limit:
                todo = todo[: args.limit]
            statuses: Counter[str] = Counter()
            failures = 0
            for index, block in enumerate(todo, start=1):
                try:
                    service.get_or_compute(
                        block.block_key,
                        feature_set,
                        (block.latitude, block.longitude),
                        "hdb_registry_onemap",
                        _compute_fn(feature_set, block, routing, availability, carpark_repo),
                        computed_for="backfill",
                        reuse=False,
                    )
                    db.commit()
                    statuses["computed"] += 1
                except Exception as exc:  # one block failing must not stop the backfill
                    db.rollback()
                    failures += 1
                    statuses[type(exc).__name__] += 1
                    continue
                if index % 200 == 0:
                    print(f"{feature_set}: {index}/{len(todo)}", file=sys.stderr, flush=True)
            db.commit()
            report[feature_set] = {
                "blocks_considered": len(blocks),
                "blocks_computed": len(todo) - failures,
                "failures": failures,
                "outcomes": dict(statuses),
            }
        if routing is not None:
            report["routing_calls"] = dict(routing.calls)
    return report


def cmd_publish(args: argparse.Namespace) -> dict[str, Any]:
    feature_sets = tuple(f.strip() for f in args.features.split(",") if f.strip())
    with SessionLocal() as db:
        result = build_and_publish(db, feature_sets=feature_sets, publish=not args.no_publish)
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
    # Publish every feature set so one version always carries all comparisons.
    publish_args = argparse.Namespace(features=",".join(FEATURE_SETS), no_publish=False)
    return {"measure": measured, "publish": cmd_publish(publish_args)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    load = sub.add_parser("load-registry", help="Load data_pipeline/fixtures/hdb_blocks.json into PostgreSQL")
    load.add_argument("--fixture", default=str(Path(FIXTURES) / "hdb_blocks.json"))
    load.set_defaults(func=cmd_load_registry)

    sub.add_parser("status", help="Show registry and measurement coverage").set_defaults(func=cmd_status)

    for name, func in (("measure", cmd_measure), ("refresh", cmd_refresh)):
        command = sub.add_parser(name)
        command.add_argument("--features", default=",".join(OFFLINE_FEATURES))
        command.add_argument("--town", help="Limit to one HDB town code, e.g. AMK")
        command.add_argument("--limit", type=int, default=0)
        command.add_argument("--force", action="store_true", help="Recompute even current results")
        command.add_argument("--allow-paid-routing", action="store_true")
        command.add_argument("--no-publish", action="store_true")
        command.set_defaults(func=func)

    publish = sub.add_parser("publish", help="Build and publish a benchmark version from stored measurements")
    publish.add_argument("--features", default=",".join(FEATURE_SETS))
    publish.add_argument("--no-publish", action="store_true", help="Build for inspection without publishing")
    publish.set_defaults(func=cmd_publish)

    args = parser.parse_args(argv)
    print(json.dumps(args.func(args), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
